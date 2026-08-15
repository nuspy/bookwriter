"""Finestra principale della GUI: barra progetto + pannelli sulle funzioni del core."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
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
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, func, select

from bookwriter.atomize.sections import build_sections, plan_sections
from bookwriter.authoring import expand_topic, start_authoring
from bookwriter.coherence import coherence_pass_compose
from bookwriter.compose import compose_outline, compose_to_markdown
from bookwriter.compose.narrative import FIDELITY_FAITHFUL, FIDELITY_FREE
from bookwriter.config import (
    DEFAULT_BASE_URL,
    PROVIDERS,
    SUPPORTED_LANGUAGES,
    AppConfig,
    ModelRoles,
    ProjectSettings,
    embedding_profile,
    language_tag,
    load_global_config,
)
from bookwriter.batch import (
    ANNULLATO,
    ATTESA,
    FALLITO,
    FATTO,
    IN_CORSO,
    INTERROTTO,
    QueuedBook,
    language_cost_group,
    load_queue,
    run_queue,
    save_queue,
    sort_queue_by_language_cost,
)
from bookwriter.db import project_schema
from bookwriter.db.project_schema import Document, Node, make_engine
from bookwriter.exporter import (
    export_filename,
    export_outline,
    export_wiki,
    latest_outline_id,
    outline_to_markdown,
    write_output,
)
from bookwriter.gui.i18n import UI_LANGUAGES, current_language, set_language, t
from bookwriter.gui.worker import Worker
from bookwriter.ingest import pipeline
from bookwriter.ingest.loaders import load_document
from bookwriter.interact import (
    build_brief,
    format_brief,
    integrate_answer,
    list_intents,
    list_open_questions,
    propose_questions,
)
from bookwriter.llm import LMStudioClient, ModelInfo
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
    rebuild_outline,
    rename_outline,
    retitle_outline,
    subdivide_sections,
)
from bookwriter import preview
from bookwriter.projects import ProjectManager
from bookwriter.rewrite import preview_pairs, rewrite_document, write_result
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
from bookwriter.update import revise_draft, write_revision, write_revision_html
from bookwriter.update.revise import split_draft


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
        # Coda di lavorazione: vive quanto la sessione, come i libri che ci si mettono dentro.
        self._queue: list[QueuedBook] = []
        self._queue_stop = False
        self._stop_requested = False
        # Modelli letti da LM Studio (None = mai interrogato in questa sessione).
        self._models: list[ModelInfo] | None = None
        # Elenco idee: totale noto, selezione da conservare fra un aggiornamento e l'altro.
        self._total_ideas = 0
        self._selected_idea_id: int | None = None
        self._ideas_timer = QTimer(self)
        self._ideas_timer.setInterval(4000)
        self._ideas_timer.timeout.connect(self._refresh_ideas)
        # Rilettura del libro in scrittura: dal disco, dove ogni brano finito è già salvato.
        # Un timer invece di un segnale perché il testo cresce anche quando a lavorare è la
        # coda, e quella vive in un thread che non conosce questa scheda.
        self._reading_timer = QTimer(self)
        self._reading_timer.setInterval(6000)
        self._reading_timer.timeout.connect(self._tick_reading)
        self._reading_timer.start()
        self._read_last: str | None = None
        # Consumo token: i client creati per l'operazione in corso vengono letti ogni 10 s.
        self._live_clients: list[tuple[LMStudioClient, Path]] = []
        self._usage_clients: list[tuple[LMStudioClient, Path]] = []
        self._usage_rows: dict[tuple[int, str], int | None] = {}
        self._usage_activity = ""
        self._usage_timer = QTimer(self)
        self._usage_timer.setInterval(10_000)
        self._usage_timer.timeout.connect(self._tick_usage)

        set_language(load_global_config().ui.language)
        self._build_central()

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)
        # Interruzione: una traduzione dura ore, e chi la lancia deve poterla fermare senza
        # chiudere il programma e senza perdere quello che è già stato tradotto.
        self.stop_button = QPushButton()
        self._label_stop_button()
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self._request_stop)
        self.statusBar().addPermanentWidget(self.stop_button)
        self.statusBar().showMessage(t("Pronto"))
        self._reload_projects()

    def _build_central(self) -> None:
        """Costruisce barra e schede. Richiamabile per applicare subito un cambio di lingua."""
        self.setWindowTitle("bookwriter")
        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(self._build_project_bar())
        self.tabs = QTabWidget()
        # Ogni scheda ha un titolo funzionale, un aggiornatore dei dati e l'indicazione se
        # serva un progetto attivo. Il dispatch è per indice, così i titoli restano liberi
        # di cambiare senza rompere la logica.
        self._tab_refreshers: list[tuple[Callable[[], None] | None, bool]] = []
        tab_specs = [
            (self._tab_ingest(), t("1 · Importa testi"), None, True),
            (self._tab_memory(), t("2 · Prepara contenuti"), self._refresh_memory_tab, True),
            (self._tab_organize(), t("3 · Componi il libro"), self._refresh_compose_lists, True),
            (self._tab_outline(), t("Struttura (modifica)"), self._refresh_outline_combo, True),
            (self._tab_style(), t("Stile di scrittura"), self._refresh_styles, True),
            (self._tab_interact(), t("Completa i contenuti"),
             lambda: self._refresh_questions(self.pm.paths(self.pm.active_slug())), True),
            (self._tab_brief(), t("Riepilogo progetto"), self._do_brief, True),
            (self._tab_rewrite(), t("Riscrivi in altro stile"), self._refresh_rewrite_lists, True),
            (self._tab_translate(), t("Traduci"), self._refresh_translate_lists, True),
            (self._tab_queue(), t("Coda di lavorazione"), self._refresh_queue_table, True),
            (self._tab_reading(), t("Leggi il libro"), lambda: self._refresh_reading(forza=True), True),
            (self._tab_authoring(), t("Scrivi da zero"), self._refresh_author_lists, True),
            (self._tab_update(), t("Aggiorna un libro"), None, True),
            (self._tab_usage(), t("Consumi dei modelli"), self._refresh_usage, False),
            (self._tab_settings(), t("Impostazioni"), self._refresh_settings, False),
        ]
        for widget, title, refresher, needs_project in tab_specs:
            self.tabs.addTab(widget, title)
            self._tab_refreshers.append((refresher, needs_project))
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs)
        self.setCentralWidget(central)  # Qt elimina il precedente, se c'era
        # Il pulsante di interruzione vive nella barra di stato, fuori dalle schede: al cambio
        # lingua non verrebbe ricostruito, e resterebbe l'unica parola italiana nella finestra.
        if hasattr(self, "stop_button"):
            self._label_stop_button()

    def _on_tab_changed(self, index: int) -> None:
        """Aggiorna il pannello selezionato con i dati correnti."""
        if not 0 <= index < len(self._tab_refreshers):
            return
        refresher, needs_project = self._tab_refreshers[index]
        if refresher is None or (needs_project and not self.pm.active_slug()):
            return
        refresher()

    # ------------------------------------------------------------------ #
    #  infrastruttura
    # ------------------------------------------------------------------ #
    def _run_bg(
        self,
        fn: Callable,
        on_done: Callable,
        busy: str = "Elaborazione…",
        progressable: bool = False,
        on_error: Callable[[str], None] | None = None,
        on_note: Callable[[object], None] | None = None,
        stoppable: bool = False,
    ) -> None:
        # Le operazioni lunghe (quelle con avanzamento) non si sovrappongono: due stesure in
        # parallelo scriverebbero due volte gli stessi capitoli.
        if progressable and self._workers:
            QMessageBox.information(
                self, t("Operazione in corso"),
                t("C'è già un'operazione in corso. Aspetta che finisca prima di avviarne un'altra."),
            )
            return
        # 'busy' resta in italiano: è anche la chiave con cui si registra il consumo.
        self.statusBar().showMessage(t(busy))
        self._begin_usage(busy)
        self.progress_bar.setRange(0, 0)  # indeterminato finché non arriva un progresso reale
        self.progress_bar.setVisible(True)
        self._stop_requested = False
        self.stop_button.setEnabled(True)
        self._label_stop_button()
        self.stop_button.setVisible(stoppable)
        worker = Worker(fn, progressable=progressable, narrating=on_note is not None)
        self._workers.add(worker)

        def _finished(result: object) -> None:
            self._workers.discard(worker)
            self._end_usage()
            self.statusBar().showMessage(t("Pronto"))
            self.progress_bar.setVisible(False)
            self.stop_button.setVisible(False)
            on_done(result)

        def _error(msg: str) -> None:
            self._workers.discard(worker)
            self._end_usage()
            self.statusBar().showMessage(t("Errore"))
            self.progress_bar.setVisible(False)
            self.stop_button.setVisible(False)
            if on_error is not None:
                on_error(msg)
            else:
                QMessageBox.critical(self, t("Errore"), msg)

        def _prog(done: int, total: int) -> None:
            if total <= 0:
                return
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
            self.progress_bar.setFormat("%p%")
            self.progress_bar.setTextVisible(True)
            # Su lavori lunghi serve anche il conteggio: la sola barra non dice quanto manca.
            self.statusBar().showMessage(
                t("{0} — {1} di {2} ({3}%)").format(t(busy), done, total, round(100 * done / total))
            )

        worker.signals.finished.connect(_finished)
        worker.signals.error.connect(_error)
        worker.signals.progress.connect(_prog)
        if on_note is not None:
            worker.signals.note.connect(on_note)
        self.pool.start(worker)

    def _label_stop_button(self) -> None:
        self.stop_button.setText(t("Interrompi"))
        self.stop_button.setToolTip(
            t("Ferma il lavoro alla fine del brano in corso. Quello già elaborato resta salvato: "
              "riavviando la stessa operazione si riprende da lì.")
        )

    def _request_stop(self) -> None:
        """Chiede al lavoro in corso di fermarsi appena finito il brano che ha per le mani."""
        self._stop_requested = True
        self.stop_button.setEnabled(False)
        self.stop_button.setText(t("Interruzione…"))
        self.statusBar().showMessage(
            t("Mi fermo alla fine del brano in corso; quanto fatto resta salvato.")
        )

    @staticmethod
    def _hint(text: str) -> QLabel:
        """Riga di aiuto in cima a un pannello: spiega a cosa serve, in parole semplici."""
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: palette(mid); padding: 2px 0 6px 0;")
        return lbl

    def _active(self) -> tuple[str, object] | None:
        slug = self.pm.active_slug()
        if not slug:
            QMessageBox.warning(self, t("Nessun progetto"), t("Crea o seleziona prima un progetto."))
            return None
        return slug, self.pm.paths(slug)

    def _client(self, slug: str) -> LMStudioClient:
        cfg = self.pm.config_for(slug)
        client = LMStudioClient(cfg.llm, cfg.embedding)
        self._live_clients.append((client, self.pm.paths(slug).db))
        return client

    # ---- consumo token: rilevazione durante l'operazione -------------------
    def _begin_usage(self, busy: str) -> None:
        """Prende in carico i client creati per questa operazione e avvia la misura."""
        self._usage_activity = busy.strip().rstrip("…. ")
        self._usage_clients = self._live_clients
        self._live_clients = []
        self._usage_rows = {}
        if self._usage_clients:
            self._save_usage()
            self._usage_timer.start()

    def _tick_usage(self) -> None:
        self._save_usage()
        self._refresh_usage()

    def _end_usage(self) -> None:
        self._usage_timer.stop()
        self._save_usage()
        for client, _db in self._usage_clients:
            client.close()
        self._usage_clients = []
        self._refresh_usage()

    def _save_usage(self) -> None:
        """Scrive i token consumati finora; la riga di ogni modello viene aggiornata in posto.

        È una rilevazione accessoria: se il database è momentaneamente occupato si rimanda al
        giro successivo, senza interrompere il lavoro in corso.
        """
        try:
            self._write_usage()
        except SQLAlchemyError:
            pass

    def _write_usage(self) -> None:
        for client, db in self._usage_clients:
            for model, counts in client.usage_by_model().items():
                key = (id(client), model)
                price_in, price_out = self._model_price(model)
                self._usage_rows[key] = project_schema.save_usage(
                    db,
                    row_id=self._usage_rows.get(key),
                    activity=self._usage_activity,
                    model=model,
                    prompt_tokens=counts["prompt_tokens"],
                    completion_tokens=counts["completion_tokens"],
                    calls=counts["calls"],
                    price_in=price_in,
                    price_out=price_out,
                )

    def _model_price(self, model: str) -> tuple[float, float]:
        """Listino del modello, dal catalogo del fornitore. Zero per i modelli locali."""
        for info in self._models or []:
            if info.id == model:
                return (info.price_in or 0.0, info.price_out or 0.0)
        return (0.0, 0.0)

    def _format_combo(self) -> QComboBox:
        """Tendina del formato di uscita, uguale in ogni schermata che produce un file."""
        combo = QComboBox()
        combo.addItem(t("DOCX (Word)"), "docx")
        combo.addItem(t("PDF"), "pdf")
        combo.addItem(t("Markdown"), "md")
        combo.setToolTip(
            t("In che formato salvare il libro finito. In tutti e tre l'impaginazione "
              "dell'originale viene conservata: titoli, capitoli, stacchi di pagina, corsivi.")
        )
        return combo

    def _ask_save_path(self, suggested: str, ppaths, *, filtro: str = "") -> Path | None:
        """Chiede dove salvare col dialogo del sistema, proponendo nome e ultima cartella usata.

        La cartella scelta viene ricordata nei default globali, quindi vale anche alla riapertura
        del programma. ``None`` se l'utente annulla.
        """
        globali = load_global_config()
        cartella = globali.ui.last_export_dir or str(ppaths.exports)
        Path(cartella).mkdir(parents=True, exist_ok=True)
        estensione = Path(suggested).suffix.lstrip(".") or "docx"
        percorso, _ = QFileDialog.getSaveFileName(
            self,
            t("Salva con nome"),
            str(Path(cartella) / suggested),
            filtro or t("Documento {0} (*.{0});;Tutti i file (*)").format(estensione),
        )
        if not percorso:
            return None
        scelto = Path(percorso)
        globali.ui.last_export_dir = str(scelto.parent)
        self.pm.save_global_config(globali)
        return scelto

    def _current_config(self) -> AppConfig:
        """Impostazioni in vigore: quelle del progetto attivo, o i default globali."""
        slug = self.pm.active_slug()
        return self.pm.config_for(slug) if slug else load_global_config()

    # ------------------------------------------------------------------ #
    #  barra progetto
    # ------------------------------------------------------------------ #
    def _build_project_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addWidget(QLabel(t("Progetto:")))
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        bar.addWidget(self.project_combo, 1)
        new_btn = QPushButton(t("Nuovo…"))
        new_btn.clicked.connect(self._new_project)
        bar.addWidget(new_btn)
        self.project_info = QLabel("")
        bar.addWidget(self.project_info)
        bar.addStretch(1)
        bar.addWidget(QLabel(t("Lingua:")))
        self.ui_lang_combo = QComboBox()
        self.ui_lang_combo.setToolTip(t("Lingua dell'interfaccia (non cambia la lingua del libro)."))
        for code, label in UI_LANGUAGES:
            self.ui_lang_combo.addItem(label, code)
        self.ui_lang_combo.setCurrentIndex(max(self.ui_lang_combo.findData(current_language()), 0))
        self.ui_lang_combo.currentIndexChanged.connect(self._on_ui_language_changed)
        bar.addWidget(self.ui_lang_combo)
        return bar

    def _on_ui_language_changed(self, _index: int) -> None:
        """Cambia lingua e ricostruisce l'interfaccia; la scelta vale per tutti i progetti."""
        code = self.ui_lang_combo.currentData()
        if not code or code == current_language():
            return
        if self._workers:
            QMessageBox.information(
                self, t("Operazione in corso"),
                t("Aspetta che l'operazione in corso finisca prima di cambiare lingua."),
            )
            self.ui_lang_combo.setCurrentIndex(max(self.ui_lang_combo.findData(current_language()), 0))
            return
        set_language(code)
        cfg = load_global_config()
        cfg.ui.language = code
        self.pm.save_global_config(cfg)
        self._build_central()
        self.statusBar().showMessage(t("Pronto"))
        self._reload_projects()

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
        # I segnali sono bloccati qui sopra: la prima apertura del progetto attivo non passerebbe
        # da _on_project_changed, e i documenti di lavoro della sessione scorsa resterebbero.
        if active:
            self.pm.open_project(active)
            self._load_queue()
        self._refresh_project_info()

    def _on_project_changed(self, _index: int) -> None:
        slug = self.project_combo.currentData()
        if slug:
            self.pm.set_active(slug)
            tolti = self.pm.open_project(slug)
            self._load_queue()
            if tolti:
                self.statusBar().showMessage(
                    t("Tolti {0} documenti di lavoro della sessione precedente.").format(tolti)
                )
            self._refresh_project_info()

    def _load_queue(self) -> None:
        """Rilegge dal progetto attivo la coda lasciata in sospeso."""
        attivo = self._active_silent()
        self._queue[:] = load_queue(attivo[1].db) if attivo else []
        self._refresh_queue_table()

    def _refresh_project_info(self) -> None:
        self._sync_settings_project()
        slug = self.pm.active_slug()
        if not slug:
            self.project_info.setText(t("nessun progetto"))
            return
        info = self.pm.info(slug)
        db = self.pm.paths(slug).db
        nodes = graph_stats(db)["nodes"]
        words = self._word_estimate(db)
        target = info.get("target_words")
        wtxt = f"{words}" + (f"/{target}" if target else "")
        self.project_info.setText(
            t("parole: {0} · doc: {1} · chunk: {2} · nodi: {3}").format(wtxt, info['documents'], info['chunks'], nodes)
        )

    def _word_estimate(self, db) -> int:
        """Parole stimate del futuro libro (somma delle parole dei nodi verbatim)."""
        with Session(make_engine(db)) as ses:
            texts = ses.exec(select(Node.raw_text)).all()
        return sum(len(t.split()) for t in texts if t)

    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, t("Nuovo progetto"), t("Nome del progetto:"))
        if not ok or not name.strip():
            return
        target, ok2 = QInputDialog.getInt(self, t("Target parole"), t("Numero di parole (0 = non impostato):"), 0, 0, 10_000_000, 1000)
        self.pm.create(name.strip(), target_words=target if ok2 and target > 0 else None)
        self._reload_projects()

    # ------------------------------------------------------------------ #
    #  tab: ingestione
    # ------------------------------------------------------------------ #
    def _tab_ingest(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Passo 1 — Aggiungi i testi di partenza: appunti, capitoli, interi documenti. "
            "Verranno copiati nel progetto e spezzati in blocchi. Formati: txt, md, pdf, docx, epub.")
        ))
        btns = QHBoxLayout()
        add_files = QPushButton(t("Aggiungi file…"))
        add_files.clicked.connect(self._add_files)
        add_dir = QPushButton(t("Aggiungi cartella…"))
        add_dir.clicked.connect(self._add_dir)
        btns.addWidget(add_files)
        btns.addWidget(add_dir)
        self.recursive_cb = QCheckBox(t("Includi le sottocartelle"))
        self.recursive_cb.setChecked(True)
        btns.addWidget(self.recursive_cb)
        btns.addStretch(1)
        lay.addLayout(btns)
        lay.addWidget(QLabel(t("Trascina qui file o cartelle, oppure usa i pulsanti:")))
        self.paths_list = DropListWidget()
        self.paths_list.paths_dropped.connect(self._add_dropped)
        lay.addWidget(self.paths_list, 1)
        run = QHBoxLayout()
        import_btn = QPushButton(t("Importa i testi selezionati"))
        import_btn.clicked.connect(self._do_ingest)
        clear_btn = QPushButton(t("Svuota la lista"))
        clear_btn.clicked.connect(lambda: (self._selected_paths.clear(), self.paths_list.clear()))
        run.addWidget(import_btn)
        run.addWidget(clear_btn)
        run.addStretch(1)
        lay.addLayout(run)
        self.ingest_result = QLabel("")
        lay.addWidget(self.ingest_result)
        return w

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, t("Scegli file"))
        for f in files:
            self._selected_paths.append(Path(f))
            self.paths_list.addItem(f)

    def _add_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, t("Scegli cartella"))
        if d:
            self._selected_paths.append(Path(d))
            self.paths_list.addItem(d + t("  (cartella)"))

    def _add_dropped(self, paths: list[Path]) -> None:
        for p in paths:
            self._selected_paths.append(p)
            self.paths_list.addItem(f"{p}{t('  (cartella)') if p.is_dir() else ''}")

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

        self._run_bg(job, lambda s: (self.ingest_result.setText(s), self._refresh_project_info()), "Importo i testi…")

    # ------------------------------------------------------------------ #
    #  tab: memoria
    # ------------------------------------------------------------------ #
    def _tab_memory(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Passo 2 — Prepara il materiale importato prima di comporre il libro. "
            "Esegui i pulsanti in ordine: rendi i testi ricercabili, estrai le idee chiave, collegale tra loro. "
            "In basso puoi cercare per significato (non per parola esatta).")
        ))
        row = QHBoxLayout()
        for label, tip, handler in [
            (t("Rendi ricercabile"), t("Indicizza i testi per la ricerca per significato (embedding)."), self._do_embed_chunks),
            (t("Dividi in sezioni"),
             t("Taglia i testi in sezioni ai confini dei paragrafi e fa descrivere ognuna dal modello (titolo, temi, persone). Il testo non viene mai riscritto."),
             self._do_atomize),
            (t("Collega le idee"), t("Costruisce la rete tematica che unisce idee affini."), self._do_graph),
            (t("Esporta archivio navigabile"), t("Crea un archivio HTML con pagine e grafo delle idee."), self._do_export_wiki),
            (t("Aggiorna conteggi"), t("Ricalcola documenti, blocchi, idee e collegamenti."), lambda: self._refresh_mem_stats()),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(handler)
            row.addWidget(b)
        lay.addLayout(row)
        self.mem_stats = QLabel("")
        lay.addWidget(self.mem_stats)
        # Esito dell'ultima operazione: riga separata dai conteggi, altrimenti il refresh
        # dei conteggi la cancellerebbe subito e sembrerebbe non sia successo nulla.
        self.mem_result = QLabel("")
        self.mem_result.setWordWrap(True)
        lay.addWidget(self.mem_result)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel(t("Cerca:")))
        self.search_target = QComboBox()
        self.search_target.addItem(t("nei testi"), "chunk")
        self.search_target.addItem(t("nelle idee"), "node")
        search_row.addWidget(self.search_target)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(t("Descrivi cosa cerchi, con parole tue…"))
        self.search_edit.returnPressed.connect(self._do_search)
        sbtn = QPushButton(t("Cerca"))
        sbtn.clicked.connect(self._do_search)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(sbtn)
        lay.addLayout(search_row)
        self.search_results = QListWidget()
        lay.addWidget(self.search_results, 1)

        lay.addWidget(QLabel(t("Idee estratte (si aggiornano da sole mentre il lavoro procede):")))
        frow = QHBoxLayout()
        self.ideas_filter = QLineEdit()
        self.ideas_filter.setPlaceholderText(t("Filtra le idee per parola…"))
        self.ideas_filter.textChanged.connect(self._filter_ideas)
        frow.addWidget(self.ideas_filter, 1)
        self.ideas_count = QLabel("")
        frow.addWidget(self.ideas_count)
        lay.addLayout(frow)
        self.ideas_tree = QTreeWidget()
        self.ideas_tree.setHeaderLabels([t("Idea (in una frase)"), t("Temi"), t("Tipo")])
        self.ideas_tree.setRootIsDecorated(False)
        self.ideas_tree.setColumnWidth(0, 520)
        self.ideas_tree.itemSelectionChanged.connect(self._show_idea)
        lay.addWidget(self.ideas_tree, 3)
        self.idea_detail = QTextEdit()
        self.idea_detail.setReadOnly(True)
        self.idea_detail.setPlaceholderText(t("Seleziona un'idea per vedere il testo originale da cui viene."))
        lay.addWidget(self.idea_detail, 1)
        return w

    # ---- idee estratte: elenco che si aggiorna durante l'estrazione ---------
    _IDEAS_LIMIT = 300  # mostra le più recenti: durante il lavoro sono quelle che interessano

    def _refresh_ideas(self) -> None:
        active = self._active_silent()
        if not active:
            return
        _slug, ppaths = active
        with Session(make_engine(ppaths.db)) as ses:
            total = int(ses.exec(select(func.count()).select_from(Node)).one())
            rows = list(ses.exec(select(Node).order_by(Node.id.desc()).limit(self._IDEAS_LIMIT)))
            titles = {d.id: d.title for d in ses.exec(select(Document)).all()}

        keep = self._selected_idea_id
        self.ideas_tree.setUpdatesEnabled(False)
        self.ideas_tree.clear()
        for node in rows:
            item = QTreeWidgetItem([
                node.summary or t("(senza sintesi)"),
                ", ".join(node.themes or []),
                node.type or "",
            ])
            item.setToolTip(0, node.raw_text or "")
            item.setData(0, Qt.ItemDataRole.UserRole, {
                "id": node.id,
                "summary": node.summary or "",
                "text": node.raw_text or "",
                "themes": node.themes or [],
                "entities": node.entities or [],
                "salience": node.salience,
                "doc": titles.get(node.document_id, "?"),
            })
            self.ideas_tree.addTopLevelItem(item)
        self.ideas_tree.setUpdatesEnabled(True)
        self._total_ideas = total
        self._filter_ideas(self.ideas_filter.text())
        if keep is not None:
            self._select_idea(keep)

    def _select_idea(self, node_id: int) -> None:
        """Ripristina la selezione dopo un aggiornamento, così il dettaglio non sparisce."""
        for i in range(self.ideas_tree.topLevelItemCount()):
            item = self.ideas_tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("id") == node_id:
                self.ideas_tree.setCurrentItem(item)
                return

    def _filter_ideas(self, text: str) -> None:
        needle = (text or "").strip().lower()
        shown = 0
        for i in range(self.ideas_tree.topLevelItemCount()):
            item = self.ideas_tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            haystack = f"{data.get('summary', '')} {data.get('text', '')} {' '.join(data.get('themes') or [])}".lower()
            hidden = bool(needle) and needle not in haystack
            item.setHidden(hidden)
            shown += not hidden
        listed = self.ideas_tree.topLevelItemCount()
        label = t("{0} idee in tutto").format(self._total_ideas)
        if listed < self._total_ideas:
            label += t(" · mostrate le {0} più recenti").format(listed)
        if needle:
            label += t(" · {0} corrispondono al filtro").format(shown)
        self.ideas_count.setText(label)

    def _show_idea(self) -> None:
        items = self.ideas_tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        self._selected_idea_id = data.get("id")
        self.idea_detail.setPlainText(
            t("{0}\n\nTesto originale (parole tue, non riscritte):\n«{1}»\n\nDa: {2}\nTemi: {3}\nPersone e luoghi: {4}\nRilevanza: {5}").format(data.get('summary', ''), data.get('text', ''), data.get('doc', '?'), ', '.join(data.get('themes') or []) or '—', ', '.join(data.get('entities') or []) or '—', data.get('salience'))
        )

    def _refresh_mem_stats(self) -> None:
        active = self._active_silent()
        if not active:
            return
        _slug, ppaths = active
        s = pipeline.memory_stats(ppaths.db)
        g = graph_stats(ppaths.db)
        store = LanceStore(ppaths.vectors)
        # I conteggi dicono anche quanto è già fatto: senza questo non si capisce
        # perché un pulsante "non faccia nulla" (il lavoro era già completo).
        self.mem_stats.setText(
            t("documenti {0} · blocchi di testo {1} (già ricercabili: {2}) · idee {3} (già ricercabili: {4}) · collegamenti {5}").format(s['documents'], s['chunks'], store.count('chunk'), g['nodes'], store.count('node'), g['edges'])
        )

    def _refresh_memory_tab(self) -> None:
        self._refresh_mem_stats()
        self._refresh_ideas()

    def _mem_done(self, message: str) -> None:
        """Mostra l'esito dell'ultima operazione e aggiorna conteggi ed elenco delle idee."""
        self.mem_result.setText(message)
        self._refresh_memory_tab()

    def _do_embed_chunks(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        client = self._client(slug)
        model_id = self.pm.config_for(slug).llm.models.embedding

        def done(rep) -> None:
            if rep.embedded:
                msg = t("Resi ricercabili {0} blocchi di testo (già pronti da prima: {1}).").format(rep.embedded, rep.already)
            elif rep.already:
                msg = t("Niente da fare: tutti i {0} blocchi erano già ricercabili.").format(rep.already)
            else:
                msg = t("Nessun blocco di testo da indicizzare: importa prima dei testi (Passo 1).")
            self._mem_done(msg)

        self._run_bg(
            lambda p: embed_chunks(ppaths.db, ppaths.vectors, client, progress=p, model_id=model_id),
            done,
            "Rendo i testi ricercabili…",
            progressable=True,
        )

    def _do_atomize(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        cfg = self.pm.config_for(slug)
        model = cfg.llm.models.reasoning
        target, ok = QInputDialog.getInt(
            self, t("Dividi in sezioni"),
            t("Lunghezza indicativa di una sezione, in parole. Il taglio cade sempre a fine "
              "paragrafo, quindi il testo resta intero e nell'ordine originale."),
            400, 150, 2000, 50,
        )
        if not ok:
            return
        previste = len(plan_sections(ppaths.db, target))
        client = self._client(slug)

        def done(rep) -> None:
            self._ideas_timer.stop()
            if rep.labelled:
                msg = t("Create {0} sezioni dai tuoi testi, tutte descritte dal modello. "
                        "Il testo è quello originale, parola per parola.").format(rep.labelled)
            elif rep.sections == 0:
                msg = t("Niente da fare: i testi erano già divisi in sezioni.")
            else:
                msg = t("Nessuna sezione descritta.")
            if rep.failed:
                msg += t(" Attenzione: {0} sezioni non descritte — il modello «{1}» non ha risposto. "
                         "Verificalo nella scheda Impostazioni.").format(rep.failed, model)
            self._mem_done(msg)

        def failed(err: str) -> None:
            self._ideas_timer.stop()
            QMessageBox.critical(self, t("Errore"), err)

        self.mem_result.setText(t("{0} sezioni da descrivere.").format(previste))
        self._refresh_ideas()
        self._ideas_timer.start()
        self._run_bg(
            lambda p: build_sections(
                ppaths.db, client, model=model, language=cfg.project.language,
                target_words=target, progress=p,
            ),
            done,
            "Divido i testi in sezioni…",
            progressable=True,
            on_error=failed,
        )

    def _do_graph(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        client = self._client(slug)

        model_id = self.pm.config_for(slug).llm.models.embedding

        def job(p):
            embed_nodes(ppaths.db, ppaths.vectors, client, progress=p, model_id=model_id)
            return build_graph(ppaths.db, ppaths.vectors)

        def done(rep) -> None:
            if rep.nodes:
                self._mem_done(t("Collegate {0} idee con {1} collegamenti tematici.").format(rep.nodes, rep.edges))
            else:
                self._mem_done(t("Nessuna idea da collegare: esegui prima «Dividi in sezioni»."))

        self._run_bg(job, done, "Collego le idee…", progressable=True)

    def _do_export_wiki(self) -> None:
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        result = export_wiki(ppaths.db, ppaths.exports / "wiki")
        QMessageBox.information(self, t("Archivio esportato"), t("Wiki: {0}\nGrafo: {1}").format(result['html'], result['json']))

    def _do_search(self) -> None:
        active = self._active()
        if not active:
            return
        query = self.search_edit.text().strip()
        if not query:
            return
        slug, ppaths = active
        client = self._client(slug)
        on_nodes = self.search_target.currentData() == "node"

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
        lay.addWidget(self._hint(
            t("Passo 3 — Prima raggruppa le idee in capitoli, poi genera il libro. Due modi: "
            "«Esporta» crea un'antologia dei testi originali riordinati; «Scrivi il libro in prosa» "
            "fa riscrivere tutto all'assistente nello stile e nella lunghezza che scegli.")
        ))
        lay.addWidget(QLabel(t("① Raggruppa le idee in capitoli")))
        row = QHBoxLayout()
        row.addWidget(QLabel(t("Come ordinare:")))
        self.ordering_combo = QComboBox()
        self.ordering_combo.addItem(t("per tema"), "thematic")
        self.ordering_combo.addItem(t("in ordine di tempo"), "chronological")
        self.ordering_combo.addItem(t("come un racconto (arco narrativo)"), "narrative")
        self.ordering_combo.addItem(t("con un mio criterio"), "custom")
        row.addWidget(self.ordering_combo)
        row.addWidget(QLabel(t("Numero di capitoli (0 = automatico):")))
        self.chapters_spin = QSpinBox()
        self.chapters_spin.setRange(0, 100)
        row.addWidget(self.chapters_spin)
        row.addWidget(QLabel(t("Metodo:")))
        self.method_combo = QComboBox()
        self.method_combo.addItem(t("automatico (per similarità)"), "embedding")
        self.method_combo.addItem(t("per gruppi di idee affini"), "community")
        row.addWidget(self.method_combo)
        org_btn = QPushButton(t("Raggruppa in capitoli"))
        org_btn.clicked.connect(self._do_organize)
        row.addWidget(org_btn)
        row.addStretch(1)
        lay.addLayout(row)
        crit_row = QHBoxLayout()
        crit_row.addWidget(QLabel(t("Il tuo criterio (solo se hai scelto «con un mio criterio»):")))
        self.criterion_edit = QLineEdit()
        self.criterion_edit.setPlaceholderText(t("es. per luogo, per personaggio, domanda→risposta…"))
        crit_row.addWidget(self.criterion_edit, 1)
        lay.addLayout(crit_row)
        lay.addWidget(QLabel(t("② Genera il libro")))
        exp = QHBoxLayout()
        exp.addWidget(QLabel(t("Formato:")))
        self.format_combo = self._format_combo()
        exp.addWidget(self.format_combo)
        exp_btn = QPushButton(t("Esporta antologia (testi originali)"))
        exp_btn.clicked.connect(self._do_export)
        exp.addWidget(exp_btn)
        preview_btn = QPushButton(t("Anteprima struttura"))
        preview_btn.clicked.connect(self._do_preview)
        exp.addWidget(preview_btn)
        exp.addStretch(1)
        lay.addLayout(exp)
        comp = QHBoxLayout()
        comp.addWidget(QLabel(t("Oppure scrivi in prosa — stile:")))
        self.compose_style_combo = QComboBox()
        comp.addWidget(self.compose_style_combo, 1)
        comp.addWidget(QLabel(t("lunghezza (parole):")))
        self.compose_target = QLineEdit()
        self.compose_target.setFixedWidth(90)
        comp.addWidget(self.compose_target)
        comp.addWidget(QLabel(t("Resa:")))
        self.compose_fidelity = QComboBox()
        self.compose_fidelity.addItem(t("fedele alle tue parole"), FIDELITY_FAITHFUL)
        self.compose_fidelity.addItem(t("riscrittura libera nello stile"), FIDELITY_FREE)
        self.compose_fidelity.setToolTip(
            t("«Fedele»: conserva le tue frasi e il tuo registro, si limita a ordinarle, collegarle "
              "e togliere le ripetizioni. «Libera»: lascia riscrivere tutto nello stile scelto.")
        )
        comp.addWidget(self.compose_fidelity)
        comp_btn = QPushButton(t("Scrivi il libro in prosa"))
        comp_btn.clicked.connect(self._do_compose)
        comp.addWidget(comp_btn)
        coh_btn = QPushButton(t("Uniforma nomi e termini"))
        coh_btn.setToolTip(t("Rende coerenti nomi propri e terminologia in tutto il libro già scritto."))
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
        self.compose_style_combo.addItem(t("— prosa chiara —"), None)
        for r in project_schema.list_styles(ppaths.db):
            self.compose_style_combo.addItem(t("{0} ({1})").format(r['name'], r['origin']), r["id"])
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
            QMessageBox.warning(self, t("Nessuna struttura"), t("Prima raggruppa le idee in capitoli (Passo 3 ①)."))
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
        cfg = self.pm.config_for(slug)
        model = cfg.llm.models.reasoning
        stile = (self.compose_style_combo.currentText().rsplit(" (", 1)[0]
                 if self.compose_style_combo.currentData() else "")
        fedelta = self.compose_fidelity.currentData()
        fmt = self.format_combo.currentData()
        out = self._ask_save_path(export_filename(
            slug, style=stile, language=language_tag(cfg.project.language), words=target,
            qualifier="" if fedelta == FIDELITY_FREE else "fedele", ext=fmt,
        ), ppaths)
        if out is None:
            return

        def job(p):
            rep = compose_outline(
                ppaths.db, oid, contract, client,
                target_words=target, tolerance_pct=spec.tolerance_pct, model=model,
                language=cfg.project.language, fidelity=fedelta, progress=p,
            )
            write_output(compose_to_markdown(ppaths.db, oid), out, fmt)
            riuso = "" if rep.rewritten == rep.chapters else t(
                " ({0} capitoli riscritti, gli altri erano già validi per questi parametri)"
            ).format(rep.rewritten)
            return f"{rep.summary()}{riuso}\n" + t("Esportato: {0}").format(out)

        self._run_bg(job, lambda s: self.outline_preview.setPlainText(s), "Scrivo il libro in prosa…", progressable=True)

    def _do_coherence(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        oid = latest_outline_id(ppaths.db)
        if not oid:
            QMessageBox.warning(self, t("Ancora nessun libro"), t("Prima raggruppa in capitoli e scrivi il libro (Passo 3)."))
            return
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.quality
        def done(r) -> None:
            lines = [t("Coerenza: {0} sostituzioni, {1} varianti uniformate.").format(r['replacements'], len(r['mappings']))]
            for issue in r.get("timeline_issues") or []:
                lines.append(t("⏱ timeline: {0}").format(issue))
            for rep in r.get("repetitions") or []:
                lines.append(t("↺ ripetizione: {0}").format(rep))
            self.outline_preview.setPlainText("\n".join(lines))

        self._run_bg(lambda: coherence_pass_compose(ppaths.db, oid, client, model=model), done, "Uniformo nomi e termini…")

    def _do_organize(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        client = self._client(slug)
        cfg = self.pm.config_for(slug)
        model = cfg.llm.models.reasoning
        language = cfg.project.language
        method = self.method_combo.currentData()
        chapters = self.chapters_spin.value() or None
        ordering = self.ordering_combo.currentData()
        criterion = self.criterion_edit.text().strip()
        if ordering == "custom" and not criterion:
            QMessageBox.warning(self, t("Manca il criterio"), t("Hai scelto «con un mio criterio»: scrivi qual è nel campo apposito."))
            return

        def job(p) -> str:
            if ordering == "thematic":
                rep = organize_thematic(
                    ppaths.db, ppaths.vectors, method=method, chapters=chapters,
                    client=client, model=model, language=language, progress=p,
                )
            elif ordering == "chronological":
                rep = organize_chronological(ppaths.db, chapters=chapters)
            elif ordering == "narrative":
                rep = organize_narrative(ppaths.db, ppaths.vectors, client, chapters=chapters, model=model)
            else:
                rep = organize_custom(ppaths.db, client, criterion, model=model)
            return t("{0} (ordinamento: {1})").format(rep.summary(), ordering)

        self._run_bg(job, lambda s: self.statusBar().showMessage(s), "Raggruppo in capitoli…", progressable=True)

    def _do_export(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        fmt = self.format_combo.currentData()
        oid = latest_outline_id(ppaths.db)
        if not oid:
            QMessageBox.warning(self, t("Nessuna struttura"), t("Prima raggruppa le idee in capitoli (Passo 3 ①)."))
            return
        cfg = self.pm.config_for(slug)
        out = self._ask_save_path(export_filename(
            slug, qualifier="antologia", language=language_tag(cfg.project.language), ext=fmt,
        ), ppaths)
        if out is None:
            return
        self._run_bg(
            lambda: str(export_outline(ppaths.db, oid, out, fmt=fmt)),
            lambda p: QMessageBox.information(self, t("Esportato"), p),
            "Export…",
        )

    def _do_preview(self) -> None:
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        oid = latest_outline_id(ppaths.db)
        if not oid:
            QMessageBox.warning(self, t("Nessuna struttura"), t("Prima raggruppa le idee in capitoli (Passo 3 ①)."))
            return
        self.outline_preview.setPlainText(outline_to_markdown(ppaths.db, oid))

    # ------------------------------------------------------------------ #
    #  tab: outline (editing manuale della struttura)
    # ------------------------------------------------------------------ #
    def _tab_outline(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Sistema a mano l'indice del libro: trascina per riordinare i capitoli e spostare le idee, "
            "doppio clic per rinominare un capitolo. Al termine premi «Salva la struttura».")
        ))
        top = QHBoxLayout()
        top.addWidget(QLabel(t("Indice da modificare:")))
        self.outline_combo = QComboBox()
        self.outline_combo.currentIndexChanged.connect(self._load_outline_tree)
        top.addWidget(self.outline_combo, 1)
        reload_btn = QPushButton(t("Ricarica"))
        reload_btn.clicked.connect(self._load_outline_tree)
        top.addWidget(reload_btn)
        lay.addLayout(top)
        self.outline_tree = QTreeWidget()
        self.outline_tree.setHeaderLabels([t("Capitoli e idee")])
        self.outline_tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.outline_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.outline_tree, 1)
        btns = QHBoxLayout()
        for label, handler in [
            (t("Aggiungi capitolo"), self._outline_add_chapter),
            (t("Rinomina capitolo"), self._outline_rename),
            (t("Elimina selezionato"), self._outline_delete),
            (t("Dividi i capitoli in sezioni"), self._do_subdivide),
            (t("Salva la struttura"), self._outline_save),
            (t("Rigenera i titoli"), self._do_retitle),
            (t("Titolo del libro…"), self._do_rename_outline),
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
            self.outline_combo.addItem(t("#{0} {1} ({2})").format(o['id'], o['name'], o['ordering']), o["id"])
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
        ci = QTreeWidgetItem([t("Nuovo capitolo")])
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
            lambda p: subdivide_sections(
                ppaths.db, ppaths.vectors, oid, client=client, model=model, progress=p,
            ),
            lambda r: QMessageBox.information(self, t("Sezioni"), t("Create {0} sezioni (visibili in export/stesura).").format(r['sections'])),
            "Divido i capitoli in sezioni…",
            progressable=True,
        )

    def _do_rename_outline(self) -> None:
        """Il nome della struttura è il titolo che compare in testa al libro esportato."""
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        oid = self.outline_combo.currentData()
        if oid is None:
            QMessageBox.warning(self, t("Nessuna struttura"), t("Prima raggruppa le idee in capitoli (Passo 3 ①)."))
            return
        attuale = self.outline_combo.currentText().split(" ", 1)[-1].rsplit(" (", 1)[0]
        titolo, ok = QInputDialog.getText(
            self, t("Titolo del libro"),
            t("Titolo che comparirà in testa al libro esportato:"), text=attuale,
        )
        if not ok or not titolo.strip():
            return
        rename_outline(ppaths.db, int(oid), titolo)
        self._refresh_outline_combo()
        self.statusBar().showMessage(t("Titolo del libro: {0}").format(titolo.strip()))

    def _do_retitle(self) -> None:
        """Rifà i titoli dei capitoli senza toccare come sono raggruppate le sezioni."""
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        oid = self.outline_combo.currentData()
        if oid is None:
            QMessageBox.warning(self, t("Nessuna struttura"), t("Prima raggruppa le idee in capitoli (Passo 3 ①)."))
            return
        cfg = self.pm.config_for(slug)
        client = self._client(slug)
        self._run_bg(
            lambda p: retitle_outline(
                ppaths.db, int(oid), client, model=cfg.llm.models.reasoning,
                language=cfg.project.language, progress=p,
            ),
            lambda k: (self._load_outline_tree(), self.statusBar().showMessage(
                t("Rigenerati i titoli di {0} capitoli.").format(k))),
            "Rigenero i titoli…",
            progressable=True,
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
        QMessageBox.information(self, t("Salvato"), t("Struttura dell'outline aggiornata."))

    # ------------------------------------------------------------------ #
    #  tab: stile
    # ------------------------------------------------------------------ #
    def _tab_style(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Crea uno «stile di scrittura» e provalo su un testo. Uno stile può essere appreso da un "
            "esempio, descritto a parole, o combinato da più autori. Gli stili qui creati si possono "
            "usare nei passi Componi, Riscrivi e Traduci.")
        ))
        row = QHBoxLayout()
        for label, handler in [
            (t("Impara da un testo…"), self._do_style_extract),
            (t("Descrivi a parole…"), self._do_style_abstract),
            (t("Combina autori…"), self._do_style_authors),
            (t("Importa dalla libreria…"), self._do_style_import_global),
        ]:
            b = QPushButton(label)
            b.clicked.connect(handler)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel(t("I tuoi stili (selezionane uno per provarlo):")))
        self.styles_list = QListWidget()
        lay.addWidget(self.styles_list, 1)
        lay.addWidget(QLabel(t("Testo di prova da riscrivere nello stile scelto:")))
        self.style_input = QTextEdit()
        self.style_input.setPlaceholderText(t("Incolla qui un testo…"))
        lay.addWidget(self.style_input, 1)
        apply_btn = QPushButton(t("Riscrivi il testo nello stile scelto"))
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
            QMessageBox.information(self, t("Libreria vuota"), t("Nessuno stile nella libreria globale."))
            return
        names = [g["name"] for g in globs]
        name, ok = QInputDialog.getItem(self, t("Importa stile globale"), t("Stile:"), names, 0, False)
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
        name, ok = QInputDialog.getText(self, t("Nome profilo"), t("Nome:"))
        if not ok or not name.strip():
            return
        client, model = self._client(slug), self.pm.config_for(slug).llm.models.reasoning
        text = Path(file).read_text(encoding="utf-8", errors="replace")

        def job():
            prof = extract_style(text, client, name=name.strip(), model=model)
            project_schema.save_style(ppaths.db, prof.name, prof.origin, prof.model_dump())
            return prof.name

        self._run_bg(job, lambda _n: self._refresh_styles(), "Imparo lo stile…")

    def _do_style_abstract(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        name, ok = QInputDialog.getText(self, t("Nome profilo"), t("Nome:"))
        if not ok or not name.strip():
            return
        desc, ok2 = QInputDialog.getMultiLineText(self, t("Descrizione stile"), t("Descrivi lo stile desiderato:"))
        if not ok2 or not desc.strip():
            return
        client, model = self._client(slug), self.pm.config_for(slug).llm.models.reasoning

        def job():
            prof = abstract_style(desc, client, name=name.strip(), model=model)
            project_schema.save_style(ppaths.db, prof.name, prof.origin, prof.model_dump())
            return prof.name

        self._run_bg(job, lambda _n: self._refresh_styles(), "Definisco lo stile…")

    def _do_style_authors(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        name, ok = QInputDialog.getText(self, t("Nome profilo"), t("Nome:"))
        if not ok or not name.strip():
            return
        specs_text, ok2 = QInputDialog.getMultiLineText(
            self, t("Autori"), t("Una riga per autore: Autore:peso:dim1,dim2"), t("Calvino:0.7:sintassi,ritmo\nHemingway:0.3:lessico")
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

        self._run_bg(job, lambda _n: self._refresh_styles(), "Combino gli autori…")

    def _do_style_apply(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        item = self.styles_list.currentItem()
        text = self.style_input.toPlainText().strip()
        if item is None or not text:
            QMessageBox.warning(self, t("Manca qualcosa"), t("Seleziona un profilo e inserisci un testo."))
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
            "Riscrivo nello stile…",
        )

    # ------------------------------------------------------------------ #
    #  tab: interazione
    # ------------------------------------------------------------------ #
    def _tab_interact(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("L'assistente analizza cosa manca al libro e propone domande. Ogni tua risposta diventa "
            "un nuovo contenuto, pronto per essere organizzato e scritto.")
        ))
        top = QHBoxLayout()
        ask_btn = QPushButton(t("Trova cosa manca (proponi domande)"))
        ask_btn.clicked.connect(self._do_ask)
        top.addWidget(ask_btn)
        top.addStretch(1)
        lay.addLayout(top)
        lay.addWidget(QLabel(t("Domande aperte (selezionane una per rispondere):")))
        self.questions_list = QListWidget()
        lay.addWidget(self.questions_list, 1)
        ans = QHBoxLayout()
        self.answer_edit = QLineEdit()
        self.answer_edit.setPlaceholderText(t("Scrivi qui la tua risposta…"))
        integ = QPushButton(t("Aggiungi la risposta al libro"))
        integ.clicked.connect(self._do_answer)
        ans.addWidget(self.answer_edit, 1)
        ans.addWidget(integ)
        lay.addLayout(ans)
        lay.addWidget(QLabel(t("Decisioni e scelte salvate:")))
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
            "Cerco cosa manca…",
        )

    def _do_answer(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        text = self.answer_edit.text().strip()
        item = self.questions_list.currentItem()
        if not text or item is None:
            QMessageBox.warning(self, t("Manca qualcosa"), t("Seleziona una domanda e scrivi una risposta."))
            return
        qid = int(item.text().split()[0].lstrip("#"))
        client = self._client(slug)
        self._run_bg(
            lambda: integrate_answer(ppaths.db, ppaths.vectors, client, qid, text),
            lambda nid: (self.answer_edit.clear(), self._refresh_questions(ppaths), self._refresh_project_info()),
            "Aggiungo la risposta al libro…",
        )

    # ------------------------------------------------------------------ #
    #  tab: brief
    # ------------------------------------------------------------------ #
    def _tab_brief(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Riepilogo dello stato del progetto: tema, decisioni prese, contenuti e domande aperte. "
            "Utile per riprendere il lavoro in una nuova sessione.")
        ))
        btn = QPushButton(t("Aggiorna il riepilogo"))
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
        lay.addWidget(self._hint(
            t("Prendi un intero documento importato e riscrivilo in un nuovo stile, mantenendo la "
            "coerenza. Prova prima l'anteprima del primo capitolo, poi riscrivi tutto.")
        ))
        row = QHBoxLayout()
        row.addWidget(QLabel(t("Stile:")))
        self.rw_style_combo = QComboBox()
        row.addWidget(self.rw_style_combo, 1)
        row.addWidget(QLabel(t("Documento da riscrivere:")))
        self.rw_doc_combo = QComboBox()
        row.addWidget(self.rw_doc_combo, 1)
        row.addWidget(self._open_file_button(self.rw_doc_combo, self._refresh_rewrite_lists, transient=True))
        row.addWidget(QLabel(t("Lunghezza (opz.):")))
        self.rw_ratio = QLineEdit()
        self.rw_ratio.setPlaceholderText(t("es. 0.7 = più corto"))
        self.rw_ratio.setToolTip(t("Rapporto sulla lunghezza originale: 1 = uguale, 0.7 = ~30% più corto, 1.3 = più lungo."))
        self.rw_ratio.setFixedWidth(120)
        row.addWidget(self.rw_ratio)
        self.rw_critic = QCheckBox(t("Controlla lo stile"))
        self.rw_critic.setToolTip(
            t("Dopo ogni blocco chiede al modello se il risultato rispetta lo stile e, se serve, "
              "lo rifà: più fedele ma circa due volte e mezzo più lento.")
        )
        self.rw_critic.setChecked(True)
        row.addWidget(self.rw_critic)
        row.addWidget(QLabel(t("Formato:")))
        self.rw_format = self._format_combo()
        row.addWidget(self.rw_format)
        lay.addLayout(row)
        btns = QHBoxLayout()
        prev_btn = QPushButton(t("Anteprima (primo capitolo)"))
        prev_btn.clicked.connect(self._do_rewrite_preview)
        full_btn = QPushButton(t("Riscrivi tutto il libro"))
        full_btn.clicked.connect(self._do_rewrite_full)
        queue_btn = QPushButton(t("Aggiungi alla coda"))
        queue_btn.setToolTip(
            t("Mette questo libro in fila invece di lavorarlo subito: si scelgono nome e cartella "
              "del file, e la coda li fa uno dopo l'altro.")
        )
        queue_btn.clicked.connect(self._enqueue_rewrite)
        btns.addWidget(prev_btn)
        btns.addWidget(full_btn)
        btns.addWidget(queue_btn)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.rw_output = QTextEdit()
        self.rw_output.setReadOnly(True)
        lay.addWidget(self.rw_output, 1)
        return w

    def _open_file_button(
        self, combo: QComboBox, refresher: Callable[[], None], *, transient: bool = False
    ) -> QPushButton:
        """Pulsante «Apri un file…» accanto a una tendina di documenti.

        Il file scelto viene selezionato subito nella tendina: così la scelta resta una sola, e
        il file è riusabile (anteprima, poi tutto) senza doverlo ricaricare ogni volta. Con
        ``transient`` resta un documento di lavoro: serve per la sessione e non entra fra le
        fonti del progetto.
        """
        button = QPushButton(t("Apri un file…"))
        button.setToolTip(
            t("Scegli un file dal computer (txt, md, pdf, docx, epub) da lavorare qui. Resta "
              "disponibile per tutta la sessione e non viene aggiunto ai testi del progetto.")
            if transient
            else t("Scegli un file dal computer (txt, md, pdf, docx, epub): viene aggiunto ai "
                   "documenti del progetto e selezionato qui.")
        )
        button.clicked.connect(lambda: self._open_document(combo, refresher, transient=transient))
        return button

    def _open_document(
        self, combo: QComboBox, refresher: Callable[[], None], *, transient: bool = False
    ) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        files, _ = QFileDialog.getOpenFileNames(self, t("Scegli file"))
        if not files:
            return
        paths = [Path(f) for f in files]
        cfg = self.pm.config_for(slug)
        before = {d["id"] for d in project_schema.list_documents(ppaths.db)}

        def job() -> None:
            pipeline.ingest_paths(
                paths, db_path=ppaths.db, target_tokens=cfg.chunking.target_tokens,
                transient=transient,
            )

        def done(_result: object) -> None:
            refresher()
            documenti = project_schema.list_documents(ppaths.db)
            nuovi = [d for d in documenti if d["id"] not in before]
            # Se il file era già stato importato non nasce un documento nuovo: si seleziona quello.
            scelto = nuovi[0] if nuovi else next(
                (d for d in documenti if Path(d["path"]) in paths), None
            )
            if scelto:
                index = combo.findData(scelto["id"])
                if index >= 0:
                    combo.setCurrentIndex(index)
                self.statusBar().showMessage(t("Documento pronto: {0}").format(scelto["title"]))
            self._refresh_project_info()

        self._run_bg(job, done, "Apro il file…")

    @staticmethod
    def _repopulate(combo: QComboBox, voci: list[tuple[str, object]]) -> None:
        """Ricostruisce una tendina conservando la scelta già fatta, se è ancora fra le voci.

        Questi elenchi si ricostruiscono ogni volta che si torna sul pannello. Senza questa
        accortezza, chi accoda cinque libri di seguito deve ritrovare il suo documento in una
        tendina di quaranta ogni volta che cambia scheda.
        """
        scelto = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for etichetta, dato in voci:
            combo.addItem(etichetta, dato)
        if scelto is not None:
            indice = combo.findData(scelto)
            if indice >= 0:
                combo.setCurrentIndex(indice)
        combo.blockSignals(False)

    @staticmethod
    def _document_title(db, doc_id: int) -> str:
        """Titolo del documento, letto dal database invece che dall'etichetta della tendina."""
        for d in project_schema.list_documents(db):
            if d["id"] == doc_id:
                return d["title"] or "libro"
        return "libro"

    @staticmethod
    def _document_label(documento: dict) -> str:
        """Voce di una tendina di documenti; i file di lavoro sono dichiarati per quello che sono."""
        if documento.get("transient"):
            return t("{0} ({1} · di lavoro, non salvato nel progetto)").format(
                documento["title"], documento["format"]
            )
        return t("{0} ({1})").format(documento["title"], documento["format"])

    def _refresh_rewrite_lists(self) -> None:
        active = self._active_silent()
        if not active:
            return
        _slug, ppaths = active
        self._repopulate(self.rw_style_combo, [
            (t("{0} ({1})").format(r["name"], r["origin"]), r["id"])
            for r in project_schema.list_styles(ppaths.db)
        ])
        self._repopulate(self.rw_doc_combo, [
            (self._document_label(d), d["id"]) for d in project_schema.list_documents(ppaths.db)
        ])

    def _rewrite_setup(self):
        active = self._active()
        if not active:
            return None
        slug, ppaths = active
        style_id = self.rw_style_combo.currentData()
        doc_id = self.rw_doc_combo.currentData()
        if style_id is None or doc_id is None:
            QMessageBox.warning(self, t("Manca qualcosa"), t("Servono un profilo di stile e un documento."))
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
        cfg = self.pm.config_for(slug)
        # Il nome del file di uscita descrive documento, stile e lingua.
        documento = self._document_title(ppaths.db, int(doc_id))
        return (slug, ppaths, contract, int(doc_id), ratio, client,
                cfg.llm.models.reasoning, documento, row["name"], cfg.project.language,
                self.rw_critic.isChecked(), cfg.llm.models.quality, int(style_id))

    def _do_rewrite_preview(self) -> None:
        setup = self._rewrite_setup()
        if not setup:
            return
        _slug, ppaths, contract, doc_id, ratio, client, model, _doc, _stile, _lingua, critic, critic_model, stile_id = setup

        def job(p):
            rep = rewrite_document(
                ppaths.db, doc_id, contract, client, kind="preview", ratio=ratio, model=model,
                resume=False, progress=p, critic=critic, critic_model=critic_model,
            )
            return preview_pairs(ppaths.db, rep.run_id)

        def done(pairs):
            blocks = []
            for i, p in enumerate(pairs, 1):
                blocks.append(f"— brano {i} ({p['chapter'] or ''}) —\nORIGINALE:\n{p['original']}\n\nRISCRITTO:\n{p['rewritten']}\n")
            self.rw_output.setPlainText("\n".join(blocks))

        self._run_bg(job, done, "Anteprima della riscrittura…", progressable=True)

    def _do_rewrite_full(self) -> None:
        setup = self._rewrite_setup()
        if not setup:
            return
        _slug, ppaths, contract, doc_id, ratio, client, model, documento, stile, lingua, critic, critic_model, stile_id = setup
        fmt = self.rw_format.currentData()
        out = self._ask_save_path(
            export_filename(documento, style=stile, language=language_tag(lingua), ext=fmt), ppaths
        )
        if out is None:
            return

        def job(p):
            rep = rewrite_document(
                ppaths.db, doc_id, contract, client, kind="full", ratio=ratio, model=model,
                progress=p, critic=critic, critic_model=critic_model,
                style_profile_id=stile_id,
                should_stop=lambda: self._stop_requested,
            )
            if rep.stopped:
                return f"{rep.summary()}\n" + t(
                    "Nessun file scritto: riavvia «Riscrivi tutto il libro» per continuare da qui."
                )
            _percorso, nota = write_result(ppaths.db, rep.run_id, out, fmt)
            return f"{rep.summary()}\n" + t("Esportato: {0}").format(out) + (f"\n{nota}" if nota else "")

        self._run_bg(
            job, lambda s: self.rw_output.setPlainText(s),
            "Riscrittura completa…", progressable=True, stoppable=True,
        )

    def _enqueue_rewrite(self) -> None:
        """Mette in coda la riscrittura impostata, col suo file di destinazione."""
        setup = self._rewrite_setup()
        if not setup:
            return
        _slug, ppaths, contract, doc_id, ratio, _client, _model, documento, stile, lingua, critic, _cm, stile_id = setup
        fmt = self.rw_format.currentData()
        out = self._ask_save_path(
            export_filename(documento, style=stile, language=language_tag(lingua), ext=fmt), ppaths
        )
        if out is None:
            return
        self._enqueue(QueuedBook(
            document_id=doc_id, title=documento, out_path=out, fmt=fmt, mode="rewrite",
            contract=contract, style_name=stile, style_profile_id=stile_id,
            ratio=ratio, critic=critic,
        ))

    # ------------------------------------------------------------------ #
    #  tab: translate (modalità C)
    # ------------------------------------------------------------------ #
    def _tab_translate(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Traduci un intero documento in un'altra lingua mantenendo lo stile e i nomi coerenti. "
            "Prova prima l'anteprima del primo capitolo.")
        ))
        row = QHBoxLayout()
        row.addWidget(QLabel(t("Traduci in:")))
        self.tr_lang = QLineEdit()
        self.tr_lang.setPlaceholderText(t("es. inglese"))
        row.addWidget(self.tr_lang)
        self.tr_critic = QCheckBox(t("Controlla lo stile"))
        self.tr_critic.setToolTip(
            t("Dopo ogni blocco chiede al modello se il risultato rispetta lo stile e, se serve, "
              "lo rifà: più fedele ma circa due volte e mezzo più lento.")
        )
        self.tr_critic.setChecked(True)
        row.addWidget(self.tr_critic)
        row.addWidget(QLabel(t("Stile:")))
        self.tr_style_combo = QComboBox()
        row.addWidget(self.tr_style_combo, 1)
        row.addWidget(QLabel(t("Documento da tradurre:")))
        self.tr_doc_combo = QComboBox()
        row.addWidget(self.tr_doc_combo, 1)
        row.addWidget(self._open_file_button(self.tr_doc_combo, self._refresh_translate_lists, transient=True))
        row.addWidget(QLabel(t("Formato:")))
        self.tr_format = self._format_combo()
        row.addWidget(self.tr_format)
        lay.addLayout(row)
        btns = QHBoxLayout()
        prev_btn = QPushButton(t("Anteprima (primo capitolo)"))
        prev_btn.clicked.connect(lambda: self._do_translate(preview=True))
        full_btn = QPushButton(t("Traduci tutto il libro"))
        full_btn.clicked.connect(lambda: self._do_translate(preview=False))
        queue_btn = QPushButton(t("Aggiungi alla coda"))
        queue_btn.setToolTip(
            t("Mette questa traduzione in fila invece di avviarla subito: si scelgono nome e "
              "cartella del file, e la coda le fa una dopo l'altra.")
        )
        queue_btn.clicked.connect(self._enqueue_translation)
        btns.addWidget(prev_btn)
        btns.addWidget(full_btn)
        btns.addWidget(queue_btn)
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
        self._repopulate(self.tr_style_combo, [(t("— mantieni stile originale —"), None)] + [
            (t("{0} ({1})").format(r["name"], r["origin"]), r["id"])
            for r in project_schema.list_styles(ppaths.db)
        ])
        self._repopulate(self.tr_doc_combo, [
            (self._document_label(d), d["id"]) for d in project_schema.list_documents(ppaths.db)
        ])

    def _translate_setup(self):
        """Parametri della traduzione impostati nel pannello, o ``None`` se ne manca qualcuno."""
        active = self._active()
        if not active:
            return None
        slug, ppaths = active
        lang = self.tr_lang.text().strip()
        doc_id = self.tr_doc_combo.currentData()
        if not lang or doc_id is None:
            QMessageBox.warning(self, t("Manca qualcosa"), t("Servono la lingua e un documento."))
            return None
        style_id = self.tr_style_combo.currentData()
        if style_id is not None:
            row = project_schema.get_style_row(ppaths.db, int(style_id))
            contract = build_style_contract(StyleProfile.model_validate(row["data"]))
        else:
            contract = "Mantieni fedelmente lo stile e il registro dell'originale."
        stile_id = int(style_id) if style_id is not None else None
        return (slug, ppaths, lang, int(doc_id), contract,
                self._document_title(ppaths.db, int(doc_id)), stile_id)

    def _enqueue_translation(self) -> None:
        """Mette in coda la traduzione impostata, col suo file di destinazione."""
        setup = self._translate_setup()
        if not setup:
            return
        _slug, ppaths, lang, doc_id, contract, documento, stile_id = setup
        fmt = self.tr_format.currentData()
        out = self._ask_save_path(
            export_filename(documento, language=language_tag(lang), ext=fmt), ppaths
        )
        if out is None:
            return
        self._enqueue(QueuedBook(
            document_id=doc_id, title=documento, out_path=out, fmt=fmt, mode="translate",
            contract=contract, target_language=lang, style_profile_id=stile_id,
            critic=self.tr_critic.isChecked(),
        ))

    def _do_translate(self, *, preview: bool) -> None:
        setup = self._translate_setup()
        if not setup:
            return
        slug, ppaths, lang, doc_id, contract, documento, stile_id = setup
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning
        # Il controllo di stile usa il modello "di qualità" se ne hai configurato uno.
        critic_model = self.pm.config_for(slug).llm.models.quality
        kind = "preview" if preview else "full"
        fmt = self.tr_format.currentData()
        out = None
        if not preview:
            out = self._ask_save_path(
                export_filename(documento, language=language_tag(lang), ext=fmt), ppaths
            )
            if out is None:
                return

        def job(p):
            rep = rewrite_document(
                ppaths.db, int(doc_id), contract, client, progress=p,
                critic=self.tr_critic.isChecked(), critic_model=critic_model,
                kind=kind, mode="translate", target_language=lang, model=model, resume=not preview,
                style_profile_id=stile_id, should_stop=lambda: self._stop_requested,
            )
            if preview:
                return ("preview", preview_pairs(ppaths.db, rep.run_id))
            if rep.stopped:
                return ("full", f"{rep.summary()}\n" + t(
                    "Nessun file scritto: riavvia «Traduci tutto il libro» per continuare da qui."
                ))
            _percorso, nota = write_result(ppaths.db, rep.run_id, out, fmt)
            return ("full", f"{rep.summary()}\n" + t("Esportato: {0}").format(out) + (f"\n{nota}" if nota else ""))

        def done(result):
            kind_, payload = result
            if kind_ == "preview":
                blocks = [f"— {p['chapter'] or ''} —\nORIG:\n{p['original']}\n\nTRAD:\n{p['rewritten']}\n" for p in payload]
                self.tr_output.setPlainText("\n".join(blocks))
            else:
                self.tr_output.setPlainText(payload)

        self._run_bg(job, done, "Traduzione…", progressable=True, stoppable=True)

    # ------------------------------------------------------------------ #
    #  tab: il libro mentre nasce
    # ------------------------------------------------------------------ #
    def _tab_reading(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Il libro com'è adesso, brano per brano, mentre viene scritto. Il file arriva solo "
              "alla fine — un file a metà col nome di quello finito si scambia per il libro vero — "
              "ma il testo si può già leggere qui. L'ultima parte arrivata è evidenziata in giallo.")
        ))
        riga = QHBoxLayout()
        riga.addWidget(QLabel(t("Da leggere:")))
        self.read_combo = QComboBox()
        self.read_combo.currentIndexChanged.connect(lambda _i: self._refresh_reading(forza=True))
        riga.addWidget(self.read_combo, 1)
        self.read_follow = QCheckBox(t("segui il lavoro in corso"))
        self.read_follow.setToolTip(
            t("Aggiorna da solo mentre la lavorazione procede e resta in fondo, sull'ultimo brano "
              "arrivato. Togli la spunta per leggere con calma senza che la pagina si muova.")
        )
        self.read_follow.setChecked(True)
        riga.addWidget(self.read_follow)
        aggiorna = QPushButton(t("Aggiorna adesso"))
        aggiorna.clicked.connect(lambda: self._refresh_reading(forza=True))
        riga.addWidget(aggiorna)
        salva = QPushButton(t("Salva quello che c'è…"))
        salva.setToolTip(
            t("Scrive su file il testo prodotto finora, con un nome che dice che è parziale, "
              "così non si confonde con il libro finito.")
        )
        salva.clicked.connect(self._save_partial)
        riga.addWidget(salva)
        lay.addLayout(riga)

        self.read_view = QTextBrowser()
        self.read_view.setOpenExternalLinks(False)
        lay.addWidget(self.read_view, 1)
        self.read_status = QLabel("")
        lay.addWidget(self.read_status)
        return w

    def _tick_reading(self) -> None:
        """Rilegge il libro solo quando serve: scheda aperta, «segui» attivo, lavoro in corso."""
        if not hasattr(self, "read_view") or not self.read_follow.isChecked():
            return
        if self.tabs.currentWidget() is not self.read_view.parentWidget():
            return  # nessuno sta guardando: non si legge il disco per niente
        self._refresh_reading()

    def _refresh_reading(self, *, forza: bool = False) -> None:
        """Rilegge il testo prodotto finora e lo mostra, senza far saltare la lettura."""
        if not hasattr(self, "read_view"):
            return
        attivo = self._active_silent()
        if not attivo:
            return
        _slug, ppaths = attivo
        sorgenti = preview.sources(ppaths.db)
        scelta = self.read_combo.currentData()
        if forza or [s.ident for s in sorgenti] != [
            self.read_combo.itemData(i) and self.read_combo.itemData(i)[1]
            for i in range(self.read_combo.count())
        ]:
            self._repopulate(self.read_combo, [
                (f"{s.label} · {s.progress}" + (t(" — in corso") if s.running else ""),
                 (s.kind, s.ident)) for s in sorgenti
            ])
            scelta = self.read_combo.currentData()
        if not sorgenti:
            self.read_view.setHtml("")
            self.read_status.setText(t("Non c'è ancora niente da leggere."))
            return
        sorgente = next((s for s in sorgenti if (s.kind, s.ident) == scelta), sorgenti[0])
        testo = preview.text_of(ppaths.db, sorgente)
        if testo == getattr(self, "_read_last", None) and not forza:
            return
        nuovo = len(testo) - len(getattr(self, "_read_last", "") or "")
        self._read_last = testo

        barra = self.read_view.verticalScrollBar()
        in_fondo = barra.value() >= barra.maximum() - 40
        posizione = barra.value()
        self.read_view.setHtml(preview.to_html(testo, evidenzia_coda=max(nuovo, 0)))
        # Chi segue il lavoro vuole restare sull'ultima riga arrivata; chi sta leggendo più su
        # non vuole essere strappato dal punto in cui era.
        barra.setValue(barra.maximum() if (self.read_follow.isChecked() and in_fondo) else posizione)
        self.read_status.setText(
            t("{0} · {1} · {2} parole").format(
                sorgente.label, sorgente.progress, len(testo.split()))
            + (t(" · si aggiorna da solo") if sorgente.running and self.read_follow.isChecked() else "")
        )

    def _save_partial(self) -> None:
        """Scrive su file il testo prodotto finora, dichiarandolo parziale nel nome."""
        attivo = self._active()
        if not attivo or not getattr(self, "_read_last", None):
            return
        _slug, ppaths = attivo
        sorgente = next(
            (s for s in preview.sources(ppaths.db)
             if (s.kind, s.ident) == self.read_combo.currentData()), None
        )
        if sorgente is None:
            return
        nome = export_filename(
            sorgente.label.split(":")[-1].strip().replace(" → ", "_"),
            qualifier="parziale", ext="md",
        )
        out = self._ask_save_path(nome, ppaths)
        if out is None:
            return
        out.write_text(self._read_last, encoding="utf-8")
        self.read_status.setText(t("Salvato quello che c'era: {0}").format(out))

    # ------------------------------------------------------------------ #
    #  tab: coda di lavorazione
    # ------------------------------------------------------------------ #
    def _tab_queue(self) -> QWidget:
        w = self._queue_tab = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("I libri messi in fila da «Riscrivi» e «Traduci» vengono lavorati uno dopo l'altro. "
              "Per ciascuno decidi qui nome e cartella del file di uscita. Uno alla volta e non "
              "insieme: due libri contemporanei si contendono lo stesso modello e finiscono "
              "entrambi più tardi. Se uno non riesce, la coda prosegue con i successivi.")
        ))
        self.queue_tree = QTreeWidget()
        self.queue_tree.setHeaderLabels([
            t("Libro"), t("Operazione"), t("Formato"), t("File di uscita"), t("Stato")
        ])
        self.queue_tree.setRootIsDecorated(False)
        self.queue_tree.setColumnWidth(0, 190)
        self.queue_tree.setColumnWidth(1, 210)
        self.queue_tree.setColumnWidth(2, 70)
        self.queue_tree.setColumnWidth(3, 330)
        lay.addWidget(self.queue_tree, 1)

        riga = QHBoxLayout()
        for etichetta, azione, suggerimento in (
            (t("↑"), lambda: self._move_in_queue(-1), t("Sposta più avanti nella fila")),
            (t("↓"), lambda: self._move_in_queue(1), t("Sposta più indietro nella fila")),
            (t("Cambia destinazione…"), self._change_queue_target,
             t("Scegli nome e cartella del file per il libro selezionato")),
            (t("Togli dalla coda"), self._remove_from_queue, t("Toglie il libro selezionato")),
            (t("Ordina per costo della lingua"), self._sort_queue,
             t("Mette davanti le lingue che il modello scrive più in fretta (inglese, francese, "
               "tedesco, spagnolo, portoghese), poi ungherese, russo e ucraino, infine le altre. "
               "A parità di ore si ottengono più libri finiti.")),
        ):
            bottone = QPushButton(etichetta)
            bottone.setToolTip(suggerimento)
            bottone.clicked.connect(azione)
            riga.addWidget(bottone)
        riga.addStretch(1)
        self.queue_start = QPushButton(t("Avvia la coda"))
        self.queue_start.clicked.connect(self._do_queue_start)
        riga.addWidget(self.queue_start)
        self.queue_stop_btn = QPushButton(t("Ferma dopo il libro in corso"))
        self.queue_stop_btn.setToolTip(
            t("Il libro in lavorazione viene portato a termine: interromperlo a metà lascerebbe "
              "un file incompleto. I successivi non vengono avviati.")
        )
        self.queue_stop_btn.setEnabled(False)
        self.queue_stop_btn.clicked.connect(self._do_queue_stop)
        riga.addWidget(self.queue_stop_btn)
        lay.addLayout(riga)

        self.queue_status = QLabel(t("la coda è vuota"))
        self.queue_status.setWordWrap(True)
        lay.addWidget(self.queue_status)
        return w

    def _enqueue(self, libro: QueuedBook) -> None:
        """Mette un libro in fila, senza spostare l'utente da dove sta lavorando.

        Chi accoda di solito ne accoda parecchi di seguito: portarlo a forza sulla coda a ogni
        aggiunta lo costringerebbe a tornare indietro ogni volta e a ricercare il documento.
        """
        self._queue.append(libro)
        self._refresh_queue_table()
        self.statusBar().showMessage(
            t("«{0}» in coda: {1} libri in attesa.").format(
                libro.title, sum(1 for b in self._queue if b.state == ATTESA)
            )
        )

    def _refresh_queue_table(self) -> None:
        if not hasattr(self, "queue_tree"):
            return
        # Ridisegnare e salvare vanno insieme: ogni cambiamento della coda passa di qui, e una
        # coda di lavori da ore deve stare sul disco, non nella memoria di una finestra.
        attivo = self._active_silent()
        if attivo:
            save_queue(attivo[1].db, self._queue)
        self.queue_tree.clear()
        for libro in self._queue:
            voce = QTreeWidgetItem([
                libro.title, libro.operation, libro.fmt, str(libro.out_path),
                libro.detail or libro.state,
            ])
            colore = {FATTO: QColor("#1b5e20"), FALLITO: self._LOADED_COLOR,
                      IN_CORSO: QColor("#0d47a1")}.get(libro.state)
            if colore is not None:
                for colonna in range(5):
                    voce.setForeground(colonna, colore)
            self.queue_tree.addTopLevelItem(voce)
        in_attesa = sum(1 for b in self._queue if b.state == ATTESA)
        fatti = sum(1 for b in self._queue if b.state == FATTO)
        self.queue_status.setText(
            t("la coda è vuota") if not self._queue
            else t("{0} libri in coda · {1} in attesa · {2} completati").format(
                len(self._queue), in_attesa, fatti)
        )

    def _selected_in_queue(self) -> int:
        """Indice del libro selezionato, o -1 con un avviso se non ne è stato scelto nessuno."""
        indice = self.queue_tree.indexOfTopLevelItem(self.queue_tree.currentItem())
        if indice < 0:
            QMessageBox.information(
                self, t("Nessun libro scelto"), t("Seleziona prima un libro nella coda.")
            )
        return indice

    def _move_in_queue(self, delta: int) -> None:
        indice = self._selected_in_queue()
        nuovo = indice + delta
        if indice < 0 or not 0 <= nuovo < len(self._queue):
            return
        self._queue[indice], self._queue[nuovo] = self._queue[nuovo], self._queue[indice]
        self._refresh_queue_table()
        self.queue_tree.setCurrentItem(self.queue_tree.topLevelItem(nuovo))

    def _remove_from_queue(self) -> None:
        indice = self._selected_in_queue()
        if indice < 0:
            return
        if self._queue[indice].state == IN_CORSO:
            QMessageBox.information(
                self, t("Libro in lavorazione"),
                t("Questo libro è in lavorazione: usa «Ferma dopo il libro in corso»."),
            )
            return
        del self._queue[indice]
        self._refresh_queue_table()

    def _change_queue_target(self) -> None:
        """Nome e cartella del file di uscita per il libro selezionato."""
        indice = self._selected_in_queue()
        active = self._active()
        if indice < 0 or not active:
            return
        _slug, ppaths = active
        libro = self._queue[indice]
        scelto = self._ask_save_path(libro.out_path.name, ppaths)
        if scelto is None:
            return
        libro.out_path = scelto
        formato = scelto.suffix.lstrip(".").lower()
        if formato in ("docx", "pdf", "md"):
            # L'estensione scelta comanda: salvare un .pdf con dentro un DOCX sarebbe una trappola.
            libro.fmt = formato
        self._refresh_queue_table()

    def _sort_queue(self) -> None:
        """Rimette la coda in ordine di costo della lingua, lasciando dov'è quel che è già fatto."""
        if not self._queue:
            return
        # Sul posto, non sostituendo la lista: la lavorazione in corso tiene un riferimento
        # a questo stesso oggetto, ed è così che vede il nuovo ordine senza riavviare.
        self._queue[:] = sort_queue_by_language_cost(self._queue)
        self._refresh_queue_table()
        gruppi = [sum(1 for b in self._queue if b.state != FATTO
                      and language_cost_group(b.target_language) == g) for g in (0, 1, 2)]
        self.statusBar().showMessage(
            t("Coda riordinata: {0} lingue veloci, poi {1}, infine {2}.").format(*gruppi)
        )

    def _do_queue_stop(self) -> None:
        self._queue_stop = True
        self.queue_stop_btn.setEnabled(False)
        self.statusBar().showMessage(
            t("La coda si fermerà appena finito il libro in lavorazione.")
        )

    def _queue_to_run(self) -> list[QueuedBook]:
        """I libri che l'avvio deve lavorare, rimessi in attesa.

        Tutto ciò che non è già stato consegnato: un libro interrotto a metà è il caso più
        frequente di riavvio della coda, e riprende dal brano dove si era fermato.
        """
        da_fare = [b for b in self._queue if b.state != FATTO]
        for libro in da_fare:
            libro.state, libro.detail = ATTESA, ""
        return da_fare

    def _do_queue_start(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        da_fare = self._queue_to_run()
        if not da_fare:
            QMessageBox.information(
                self, t("Coda vuota"),
                t("Non c'è niente da lavorare. Aggiungi libri da «Riscrivi» o «Traduci»."),
            )
            return
        self._refresh_queue_table()
        self._queue_stop = False
        self.queue_stop_btn.setEnabled(True)
        cfg = self.pm.config_for(slug)
        client = self._client(slug)
        coda = self._queue

        def job(avanzamento, nota):
            def passo(indice: int, totale: int, fatti: int, blocchi: int) -> None:
                # Progresso in centesimi di libro: dice a che punto è la coda *e* il libro.
                dentro = round(100 * fatti / blocchi) if blocchi else 0
                avanzamento(indice * 100 + min(dentro, 100), totale * 100)

            return run_queue(
                coda, ppaths.db, client,
                model=cfg.llm.models.reasoning, critic_model=cfg.llm.models.quality,
                progress=passo,
                on_change=lambda i, libro: nota({"indice": i, "titolo": libro.title,
                                                 "operazione": libro.operation, "punto": libro.detail}),
                # «Ferma dopo il libro in corso» porta a termine quello che sta traducendo;
                # «Interrompi» smette subito, salvando quello che ha già fatto.
                should_stop=lambda: self._queue_stop,
                should_abort=lambda: self._stop_requested,
            )

        def done(report) -> None:
            self.queue_stop_btn.setEnabled(False)
            self._refresh_queue_table()
            self.queue_status.setText(report.summary())

        def fallito(messaggio: str) -> None:
            self.queue_stop_btn.setEnabled(False)
            self._refresh_queue_table()
            self.queue_status.setText(t("Coda interrotta: {0}").format(messaggio))

        def avanzamento(info: object) -> None:
            """Racconta a che punto è il libro in lavorazione, non solo a che punto è la coda."""
            self._refresh_queue_table()
            if isinstance(info, dict) and info.get("punto"):
                self.statusBar().showMessage(
                    t("Libro {0} di {1}: {2} — {3}").format(
                        info["indice"] + 1, len(coda), info["operazione"], info["punto"])
                )

        self._run_bg(
            job, done, "Coda di lavorazione…", progressable=True, stoppable=True,
            on_error=fallito, on_note=avanzamento,
        )

    # ------------------------------------------------------------------ #
    #  tab: authoring (modalità D)
    # ------------------------------------------------------------------ #
    def _tab_authoring(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Parti da (quasi) niente e lascia scrivere il libro all'assistente. Segui i tre passi: "
            "descrivi l'opera, fai generare le idee, genera la bozza completa.")
        ))
        lay.addWidget(QLabel(t("1) Descrivi l'opera")))
        brief = QHBoxLayout()
        self.au_theme = QLineEdit()
        self.au_theme.setPlaceholderText(t("Tema (di cosa parla)"))
        self.au_audience = QLineEdit()
        self.au_audience.setPlaceholderText(t("Per chi (pubblico)"))
        self.au_goal = QLineEdit()
        self.au_goal.setPlaceholderText(t("Obiettivo (che effetto vuoi)"))
        self.au_target = QLineEdit()
        self.au_target.setPlaceholderText(t("Lunghezza (parole)"))
        self.au_target.setFixedWidth(130)
        for wdg in (self.au_theme, self.au_audience, self.au_goal, self.au_target):
            brief.addWidget(wdg)
        start_btn = QPushButton(t("Salva la descrizione"))
        start_btn.clicked.connect(self._do_author_start)
        brief.addWidget(start_btn)
        lay.addLayout(brief)

        lay.addWidget(QLabel(t("2) Fai generare le idee (le crea l'assistente)")))
        exp = QHBoxLayout()
        self.au_topic = QLineEdit()
        self.au_topic.setPlaceholderText(t("Sotto-tema da sviluppare"))
        exp.addWidget(self.au_topic, 1)
        exp.addWidget(QLabel(t("quante:")))
        self.au_n = QSpinBox()
        self.au_n.setRange(1, 30)
        self.au_n.setValue(6)
        exp.addWidget(self.au_n)
        exp_btn = QPushButton(t("Genera idee"))
        exp_btn.clicked.connect(self._do_author_expand)
        exp.addWidget(exp_btn)
        lay.addLayout(exp)

        lay.addWidget(QLabel(t("3) Genera la bozza completa del libro")))
        draft = QHBoxLayout()
        draft.addWidget(QLabel(t("Stile:")))
        self.au_style_combo = QComboBox()
        draft.addWidget(self.au_style_combo, 1)
        draft.addWidget(QLabel(t("Formato:")))
        self.au_format = self._format_combo()
        draft.addWidget(self.au_format)
        draft_btn = QPushButton(t("Genera la bozza completa"))
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
        self.au_style_combo.addItem(t("— prosa chiara —"), None)
        for r in project_schema.list_styles(ppaths.db):
            self.au_style_combo.addItem(t("{0} ({1})").format(r['name'], r['origin']), r["id"])

    def _do_author_start(self) -> None:
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        theme = self.au_theme.text().strip()
        if not theme:
            QMessageBox.warning(self, t("Manca il tema"), t("Inserisci almeno il tema."))
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
        self.au_output.setPlainText(t("Brief registrato. Ora genera idee sui sotto-temi, poi la bozza."))
        self._refresh_project_info()

    def _do_author_expand(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        topic = self.au_topic.text().strip()
        if not topic:
            QMessageBox.warning(self, t("Manca il sotto-tema"), t("Inserisci un sotto-tema."))
            return
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning
        n = self.au_n.value()
        aud = self.au_audience.text().strip() or None
        goal = self.au_goal.text().strip() or None
        self._run_bg(
            lambda: expand_topic(ppaths.db, ppaths.vectors, client, topic=topic, n=n, audience=aud, goal=goal, model=model),
            lambda c: (self.au_output.setPlainText(t("Generate {0} idee sul tema «{1}».").format(c, topic)), self._refresh_project_info()),
            "Genero le idee…",
        )

    def _do_author_draft(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        client = self._client(slug)
        cfg = self.pm.config_for(slug)
        model = cfg.llm.models.reasoning
        language = cfg.project.language
        style_id = self.au_style_combo.currentData()
        stile = self.au_style_combo.currentText().rsplit(" (", 1)[0] if style_id is not None else ""
        if style_id is not None:
            row = project_schema.get_style_row(ppaths.db, int(style_id))
            contract = build_style_contract(StyleProfile.model_validate(row["data"]))
        else:
            contract = "Scrivi in prosa chiara, scorrevole e coerente, con buone transizioni."
        spec = project_schema.get_bookspec(ppaths.db)
        fmt = self.au_format.currentData()
        out = self._ask_save_path(export_filename(
            slug, qualifier="bozza", style=stile, language=language_tag(language),
            words=spec.target_words, ext=fmt,
        ), ppaths)
        if out is None:
            return

        def job(p):
            embed_nodes(ppaths.db, ppaths.vectors, client)
            build_graph(ppaths.db, ppaths.vectors)
            org = organize_thematic(ppaths.db, ppaths.vectors, method="embedding", name="Bozza autoriale", client=client, model=model)
            rep = compose_outline(
                ppaths.db, org.outline_id, contract, client, target_words=spec.target_words,
                tolerance_pct=spec.tolerance_pct, model=model, language=language, progress=p,
            )
            write_output(compose_to_markdown(ppaths.db, org.outline_id), out, fmt)
            return f"{rep.summary()}\nEsportato: {out}"

        self._run_bg(job, lambda s: self.au_output.setPlainText(s), "Genero la bozza del libro…", progressable=True)

    # ------------------------------------------------------------------ #
    #  tab: aggiorna un libro già scritto
    # ------------------------------------------------------------------ #
    def _tab_update(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Carica una bozza già scritta: il programma la confronta con il materiale del "
              "progetto, aggiunge ciò che manca, toglie i passaggi ripetuti e integra quelli "
              "incompleti. Il risultato è il libro intero con le modifiche in evidenza: il nuovo "
              "incorniciato di rosso, il tolto barrato, il sostituito barrato con la nuova "
              "versione in verde.")
        ))
        row = QHBoxLayout()
        load = QPushButton(t("Carica la bozza…"))
        load.clicked.connect(self._load_draft)
        row.addWidget(load)
        self.up_file = QLabel(t("nessuna bozza caricata"))
        row.addWidget(self.up_file, 1)
        self.up_integrate = QCheckBox(t("Integra i passaggi incompleti"))
        self.up_integrate.setToolTip(
            t("Quando un passaggio della bozza dice la stessa cosa del materiale ma in modo "
              "parziale, ne fa una versione unica invece di accodare il contenuto a parte.")
        )
        self.up_integrate.setChecked(True)
        row.addWidget(self.up_integrate)
        row.addWidget(QLabel(t("Formato:")))
        self.up_format = self._format_combo()
        self.up_format.setToolTip(
            t("In che formato salvare il libro aggiornato. Le marcature delle modifiche ci sono "
              "in tutti e tre; oltre a quello si ottiene sempre una copia HTML da leggere nel "
              "browser.")
        )
        row.addWidget(self.up_format)
        lay.addLayout(row)

        run = QHBoxLayout()
        analizza = QPushButton(t("Confronta e aggiorna"))
        analizza.clicked.connect(self._do_update_book)
        run.addWidget(analizza)
        run.addStretch(1)
        lay.addLayout(run)

        self.up_tree = QTreeWidget()
        self.up_tree.setHeaderLabels([t("Modifica"), t("Capitolo"), t("Testo"), t("Motivo")])
        self.up_tree.setColumnWidth(0, 110)
        self.up_tree.setColumnWidth(1, 190)
        self.up_tree.setColumnWidth(2, 460)
        self.up_tree.setRootIsDecorated(False)
        lay.addWidget(self.up_tree, 1)
        self.up_status = QLabel("")
        self.up_status.setWordWrap(True)
        lay.addWidget(self.up_status)
        return w

    def _load_draft(self) -> None:
        percorso, _ = QFileDialog.getOpenFileName(self, t("Carica la bozza…"))
        if not percorso:
            return
        try:
            self._draft = load_document(percorso)
        except Exception as exc:  # noqa: BLE001 - formato non leggibile
            QMessageBox.critical(self, t("Errore"), str(exc))
            return
        self._draft_path = Path(percorso)
        parole = len(self._draft.raw_text.split())
        capitoli = len([c for c in split_draft(self._draft.raw_text) if c.title])
        self.up_file.setText(
            t("{0} · {1} parole · {2} capitoli riconosciuti").format(self._draft_path.name, parole, capitoli)
        )

    # Simboli delle modifiche nell'elenco della revisione.
    _MARK: ClassVar[dict[str, str]] = {
        "keep": "=", "add": "+ nuovo", "remove": "− tolto", "replace": "~ integrato",
    }

    def _do_update_book(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        if not getattr(self, "_draft", None):
            QMessageBox.warning(self, t("Manca la bozza"), t("Carica prima una bozza da aggiornare."))
            return
        cfg = self.pm.config_for(slug)
        client = self._client(slug)
        testo = self._draft.raw_text
        nome = self._draft_path.stem
        integra = self.up_integrate.isChecked()
        fmt = self.up_format.currentData()
        out = self._ask_save_path(export_filename(
            nome, qualifier="aggiornato", language=language_tag(cfg.project.language), ext=fmt,
        ), ppaths)
        if out is None:
            return

        def job(p):
            report = revise_draft(
                ppaths.db, testo, client, model=cfg.llm.models.reasoning,
                integrate=integra, progress=p,
            )
            libro = write_revision(report, out, fmt, title=nome)
            html = write_revision_html(report, out.with_suffix(".html"), title=nome)
            return report, libro, html

        def done(risultato) -> None:
            report, libro, html = risultato
            self.up_tree.clear()
            for change in report.changes:
                testo_breve = (change.previous or change.text).replace(chr(10), " ")
                item = QTreeWidgetItem([
                    self._MARK.get(change.operation, change.operation),
                    change.chapter or "—",
                    testo_breve[:220],
                    change.reason,
                ])
                colore = {"add": self._LOADED_COLOR, "remove": self._LOADED_COLOR,
                          "replace": QColor("#1b5e20")}.get(change.operation)
                if colore is not None:
                    for col in range(4):
                        item.setForeground(col, colore)
                self.up_tree.addTopLevelItem(item)
            self.up_status.setText(
                t("{0}. Esportati: {1} e {2}").format(report.summary(), libro.name, html.name)
            )

        self._run_bg(job, done, "Confronto la bozza col materiale…", progressable=True)

    # ------------------------------------------------------------------ #
    #  tab: consumo dei modelli (token)
    # ------------------------------------------------------------------ #
    def _tab_usage(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Quanto lavoro hanno fatto i modelli, diviso per progetto e per attività. "
            "«Token inviati» è il testo che riceve il modello, «token generati» è quello che produce. "
            "Il conteggio si aggiorna ogni 10 secondi mentre un'operazione è in corso e resta "
            "salvato nel progetto anche dopo aver chiuso il programma.")
        ))
        self.usage_tree = QTreeWidget()
        self.usage_tree.setHeaderLabels(
            [t("Progetto / attività"), t("Esecuzioni"), t("Richieste"), t("Token inviati"),
             t("Token generati"), t("Totale"), t("Costo"), t("Modelli")]
        )
        self.usage_tree.setColumnWidth(0, 300)
        lay.addWidget(self.usage_tree, 1)
        row = QHBoxLayout()
        refresh = QPushButton(t("Aggiorna"))
        refresh.clicked.connect(self._refresh_usage)
        row.addWidget(refresh)
        row.addStretch(1)
        lay.addLayout(row)
        self.usage_status = QLabel("")
        lay.addWidget(self.usage_status)
        return w

    @staticmethod
    def _thousands(value: int) -> str:
        return f"{value:,}".replace(",", ".")

    @staticmethod
    def _money(value: float) -> str:
        """Costo in dollari; trattino quando il modello gira in locale (nessuna spesa)."""
        return f"${value:.2f}".replace(".", ",") if value else "—"

    def _refresh_usage(self) -> None:
        if not hasattr(self, "usage_tree"):
            return
        active = self.pm.active_slug()
        self.usage_tree.clear()
        grand = 0
        grand_cost = 0.0
        for project in self.pm.list():
            rows = project_schema.usage_by_activity(self.pm.paths(project.slug).db)
            totals = {
                k: sum(r[k] for r in rows)
                for k in ("runs", "calls", "prompt_tokens", "completion_tokens", "total_tokens", "cost")
            }
            grand += totals["total_tokens"]
            grand_cost += totals["cost"]
            parent = QTreeWidgetItem([
                project.name,
                str(totals["runs"]),
                str(totals["calls"]),
                self._thousands(totals["prompt_tokens"]),
                self._thousands(totals["completion_tokens"]),
                self._thousands(totals["total_tokens"]),
                self._money(totals["cost"]),
                "",
            ])
            font = parent.font(0)
            font.setBold(True)
            for col in range(self.usage_tree.columnCount()):
                parent.setFont(col, font)
            for r in rows:
                parent.addChild(QTreeWidgetItem([
                    t(r["activity"]),
                    str(r["runs"]),
                    str(r["calls"]),
                    self._thousands(r["prompt_tokens"]),
                    self._thousands(r["completion_tokens"]),
                    self._thousands(r["total_tokens"]),
                    self._money(r["cost"]),
                    ", ".join(r["models"]),
                ]))
            self.usage_tree.addTopLevelItem(parent)
            if project.slug == active:
                parent.setExpanded(True)
        running = " · misura in corso, aggiornamento ogni 10 s" if self._usage_timer.isActive() else ""
        spesa = f" · spesa {self._money(grand_cost)}" if grand_cost else ""
        self.usage_status.setText(
            t("Totale di tutti i progetti: {0} token{1}{2}").format(self._thousands(grand), spesa, running)
        )

    # ------------------------------------------------------------------ #
    #  tab: impostazioni (quale modello usare)
    # ------------------------------------------------------------------ #
    _LOADED_COLOR = QColor("#c62828")  # rosso: modello già in memoria in LM Studio

    def _tab_settings(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Scegli il progetto, la lingua in cui vuoi il libro e quali modelli di LM Studio usare. "
            "I modelli già caricati in memoria sono segnati in rosso con «(caricato)»: partono "
            "subito, gli altri vengono caricati al primo utilizzo e la prima operazione sarà più lenta.")
        ))

        head = QFormLayout()
        self.set_provider = QComboBox()
        self.set_provider.setToolTip(t("Chi esegue i modelli: il tuo computer o un servizio nel cloud."))
        for key, info in PROVIDERS.items():
            self.set_provider.addItem(info["label"], key)
        self.set_provider.currentIndexChanged.connect(self._on_provider_changed)
        head.addRow(t("Fornitore:"), self.set_provider)
        self.set_project = QComboBox()
        self.set_project.setToolTip(t("Le impostazioni qui sotto valgono per questo progetto."))
        self.set_project.currentIndexChanged.connect(self._on_settings_project_changed)
        self.set_language = QComboBox()
        self.set_language.setToolTip(
            t("Lingua di ciò che viene generato: sintesi delle idee, temi, prosa del libro. "
            "Il testo che hai importato resta com'è.")
        )
        for code, label in SUPPORTED_LANGUAGES:
            self.set_language.addItem(label, code)
        head.addRow(t("Progetto:"), self.set_project)
        head.addRow(t("Lingua dei contenuti generati:"), self.set_language)
        lay.addLayout(head)

        server = QHBoxLayout()
        self.set_url_label = QLabel(t("Indirizzo del servizio:"))
        server.addWidget(self.set_url_label)
        self.set_base_url = QLineEdit()
        self.set_base_url.setPlaceholderText(DEFAULT_BASE_URL)
        self.set_base_url.setToolTip(t("Indirizzo dell'API. Per LM Studio è il server locale (menu Developer → Server)."))
        server.addWidget(self.set_base_url, 1)
        detect = QPushButton(t("Leggi i modelli disponibili"))
        detect.setToolTip(t("Interroga il fornitore e aggiorna gli elenchi qui sotto."))
        detect.clicked.connect(lambda: self._load_models(force=True))
        server.addWidget(detect)
        lay.addLayout(server)

        keyrow = QHBoxLayout()
        self.set_key_label = QLabel(t("Chiave di accesso:"))
        keyrow.addWidget(self.set_key_label)
        self.set_api_key = QLineEdit()
        self.set_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.set_api_key.setPlaceholderText(t("incolla qui la chiave del fornitore"))
        self.set_api_key.setToolTip(
            t("Resta sul tuo computer, nel file di configurazione del progetto. "
            "In alternativa puoi impostarla come variabile d'ambiente, che ha la precedenza.")
        )
        keyrow.addWidget(self.set_api_key, 1)
        lay.addLayout(keyrow)

        self.set_reasoning = QComboBox()
        self.set_reasoning.setToolTip(t("Il modello che scrive, riscrive, traduce e riassume (ruolo 'reasoning')."))
        self.set_embedding = QComboBox()
        self.set_embedding.setToolTip(t("Il modello che confronta i significati: ricerca, raggruppamenti, collegamenti (ruolo 'embedding')."))
        self.set_heavy = QComboBox()
        self.set_heavy.setToolTip(t("Facoltativo: modello più lento ma più accurato per le riletture di qualità (ruolo 'heavy')."))
        form = QFormLayout()
        form.addRow(t("Modello che scrive i testi:"), self.set_reasoning)
        form.addRow(t("Modello che confronta i significati:"), self.set_embedding)
        form.addRow(t("Modello per la rilettura di qualità:"), self.set_heavy)
        lay.addLayout(form)
        for combo in (self.set_reasoning, self.set_embedding, self.set_heavy):
            combo.currentIndexChanged.connect(lambda _i, c=combo: self._mark_loaded_selection(c))

        self.set_as_default = QCheckBox(t("Usa questi modelli anche per i prossimi progetti"))
        self.set_as_default.setChecked(True)
        lay.addWidget(self.set_as_default)

        row = QHBoxLayout()
        save = QPushButton(t("Salva le impostazioni"))
        save.setToolTip(t("Applica la scelta al progetto attivo (le operazioni successive useranno questi modelli)."))
        save.clicked.connect(self._save_settings)
        row.addWidget(save)
        row.addStretch(1)
        lay.addLayout(row)

        self.set_status = QLabel(t("Premi «Leggi i modelli disponibili» per vedere il catalogo del fornitore."))
        self.set_status.setWordWrap(True)
        lay.addWidget(self.set_status)
        lay.addStretch(1)
        self._apply_provider_ui()  # stato iniziale del campo chiave
        return w

    def _refresh_settings(self) -> None:
        """All'apertura del pannello: mostra le impostazioni in vigore e i modelli disponibili."""
        cfg = self._current_config()
        self._sync_settings_project()
        idx = self.set_language.findData(cfg.project.language)
        self.set_language.setCurrentIndex(max(idx, 0))
        self.set_provider.blockSignals(True)
        self.set_provider.setCurrentIndex(max(self.set_provider.findData(cfg.llm.provider), 0))
        self.set_provider.blockSignals(False)
        self.set_base_url.setText(cfg.llm.base_url)
        self.set_api_key.setText(cfg.llm.api_key)
        self._apply_provider_ui()
        self._load_models()

    def _apply_provider_ui(self) -> None:
        """Mostra il campo della chiave solo per i fornitori che la richiedono."""
        info = PROVIDERS.get(self.set_provider.currentData() or "lmstudio", {})
        needs_key = bool(info.get("needs_key"))
        for widget in (self.set_key_label, self.set_api_key):
            widget.setVisible(needs_key)

    def _on_provider_changed(self, _index: int) -> None:
        """Cambiando fornitore cambiano indirizzo e catalogo dei modelli."""
        info = PROVIDERS.get(self.set_provider.currentData() or "lmstudio", {})
        self.set_base_url.setText(str(info.get("base_url", DEFAULT_BASE_URL)))
        self._apply_provider_ui()
        self._models = None  # il catalogo del fornitore precedente non vale più
        self._load_models(force=True)

    def _sync_settings_project(self) -> None:
        """Riallinea la tendina dei progetti del pannello con il progetto attivo."""
        self.set_project.blockSignals(True)
        self.set_project.clear()
        for project in self.pm.list():
            self.set_project.addItem(project.name, project.slug)
        idx = self.set_project.findData(self.pm.active_slug())
        self.set_project.setCurrentIndex(max(idx, 0))
        self.set_project.blockSignals(False)

    def _on_settings_project_changed(self, _index: int) -> None:
        slug = self.set_project.currentData()
        if not slug or slug == self.pm.active_slug():
            return
        self.pm.set_active(slug)
        self._reload_projects()  # riallinea anche la barra in alto
        self._refresh_settings()

    def _load_models(self, *, force: bool = False) -> None:
        """Interroga LM Studio in background e ricostruisce gli elenchi (cache di sessione)."""
        if self._models is not None and not force:
            self._fill_model_combos()
            return
        cfg = self._current_config()
        base = self.set_base_url.text().strip() or DEFAULT_BASE_URL
        llm_cfg = cfg.llm.model_copy(update={
            "provider": self.set_provider.currentData() or "lmstudio",
            "base_url": base,
            "api_key": self.set_api_key.text().strip(),
        })

        info = PROVIDERS.get(llm_cfg.provider, {})
        if info.get("needs_key") and not llm_cfg.resolved_api_key():
            # Senza chiave la richiesta tornerebbe solo un 401: meglio dirlo subito.
            self._models = []
            self._fill_model_combos()
            console = info.get("console") or ""
            self.set_status.setText(
                "Serve la chiave di accesso: incollala qui sopra e premi «Leggi i modelli disponibili»."
                + (f" La ottieni da {console}" if console else "")
            )
            return

        def job() -> list[ModelInfo]:
            with LMStudioClient(llm_cfg, cfg.embedding) as client:
                return client.list_models()

        def done(models: list[ModelInfo]) -> None:
            self._models = models
            self._fill_model_combos()

        def failed(msg: str) -> None:
            self._models = []
            self._fill_model_combos()
            info = PROVIDERS.get(llm_cfg.provider, {})
            hint = (
                t("Controlla la chiave di accesso") if info.get("needs_key")
                else t("Avvia il server dei modelli in LM Studio")
            )
            self.set_status.setText(t("{0} non risponde su {1}. {2}. ({3})").format(info.get('label', llm_cfg.provider), base, hint, msg))

        self._run_bg(job, done, "Leggo i modelli di LM Studio…", on_error=failed)

    def _fill_model_combos(self) -> None:
        models = self._models or []
        roles = self._current_config().llm.models
        chat_like = [m for m in models if m.type != "embeddings"]
        embed_like = [m for m in models if m.type == "embeddings"]
        # Col fallback su /v1/models il tipo non è noto: meglio mostrare tutto che nascondere.
        chat_like = chat_like or models
        embed_like = embed_like or models
        self._fill_model_combo(self.set_reasoning, chat_like, roles.reasoning)
        self._fill_model_combo(self.set_embedding, embed_like, roles.embedding)
        self._fill_model_combo(
            self.set_heavy, chat_like, roles.heavy,
            empty_label=t("— nessuno: usa il modello che scrive —"),
        )
        if not models:
            self.set_status.setText(t("Nessun modello rilevato in LM Studio."))
            return
        loaded = [m.id for m in models if m.loaded]
        text = t("{0} modelli in LM Studio · caricati ora: ").format(len(models)) + (", ".join(loaded) if loaded else t("nessuno"))
        # Se il modello scelto per scrivere non è in memoria, segnala l'alternativa già pronta.
        ready = [m.id for m in chat_like if m.loaded]
        if ready and self.set_reasoning.currentData() not in ready:
            text += t(" · «{0}» è già in memoria: sceglierlo evita l'attesa del caricamento.").format(ready[0])
        self.set_status.setText(text)

    def _fill_model_combo(
        self,
        combo: QComboBox,
        models: list[ModelInfo],
        current: str,
        *,
        empty_label: str | None = None,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        if empty_label is not None:
            combo.addItem(empty_label, "")
        for m in models:
            price = m.price_label()
            label = t("{0}  (caricato)").format(m.id) if m.loaded else m.id
            if price:
                label += t("  ·  {0}").format(price)
            combo.addItem(label, m.id)
            if m.loaded:
                combo.setItemData(combo.count() - 1, self._LOADED_COLOR, Qt.ItemDataRole.ForegroundRole)
        # Il modello configurato può non esistere nel catalogo (rimosso, o di un altro fornitore).
        if current and combo.findData(current) < 0:
            combo.insertItem(0, t("{0}  (non disponibile presso questo fornitore)").format(current), current)
        idx = combo.findData(current)
        combo.setCurrentIndex(max(idx, 0))
        combo.blockSignals(False)
        self._mark_loaded_selection(combo)

    def _mark_loaded_selection(self, combo: QComboBox) -> None:
        """Colora di rosso la casella chiusa quando il modello scelto è già caricato."""
        loaded = {m.id for m in (self._models or []) if m.loaded}
        chosen = combo.currentData()
        combo.setStyleSheet(f"color: {self._LOADED_COLOR.name()};" if chosen in loaded else "")

    def _save_settings(self) -> None:
        reasoning = self.set_reasoning.currentData() or ""
        embedding = self.set_embedding.currentData() or ""
        if not reasoning or not embedding:
            QMessageBox.warning(
                self, t("Manca un modello"),
                t("Scegli sia il modello che scrive i testi sia quello che confronta i significati."),
            )
            return
        roles = ModelRoles(reasoning=reasoning, embedding=embedding, heavy=self.set_heavy.currentData() or "")
        cfg = self._current_config()
        base = self.set_base_url.text().strip() or DEFAULT_BASE_URL
        language = self.set_language.currentData() or ""
        # Ogni famiglia di embedding vuole i suoi prefissi: li adeguiamo al modello scelto.
        profile = embedding_profile(embedding)
        new = cfg.model_copy(update={
            "embedding": profile or cfg.embedding,
            "llm": cfg.llm.model_copy(update={
                "provider": self.set_provider.currentData() or "lmstudio",
                "base_url": base,
                "api_key": self.set_api_key.text().strip(),
                "models": roles,
            }),
            "project": ProjectSettings(language=language),
        })

        slug = self.pm.active_slug()
        where: list[str] = []
        if slug:
            self.pm.save_config_for(slug, new)
            where.append("questo progetto")
        if self.set_as_default.isChecked() or not slug:
            self.pm.save_global_config(new)
            where.append("i prossimi progetti")
        self.set_status.setText(
            t("Salvato per {0}. Fornitore: {1}. Lingua: {2}. Scrive con «{3}», confronta con «{4}».").format(
                ' e '.join(where), self.set_provider.currentText(),
                self.set_language.currentText(), reasoning, embedding,
            )
            + (t(" Prefissi di ricerca adattati al modello.") if profile else "")
        )

    # ------------------------------------------------------------------ #
    def _placeholder(self, text: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(label)
        return w
