"""Finestra principale della GUI: barra progetto + pannelli sulle funzioni del core."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar


from llmswitch import Registry
from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QDoubleSpinBox,
    QSpinBox,
    QStackedWidget,
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

from bookwriter import preview
from bookwriter.atomize.sections import build_sections, plan_sections
from bookwriter.batch import (
    ATTESA,
    FALLITO,
    FATTO,
    IN_CORSO,
    QueuedBook,
    language_cost_group,
    load_queue,
    run_queue,
    save_queue,
    sort_queue_by_language_cost,
)
from bookwriter.coherence import coherence_pass_compose
from bookwriter.compose import compose_outline, compose_to_markdown
from bookwriter.compose.narrative import FIDELITY_FAITHFUL, FIDELITY_FREE
from bookwriter.config import (
    DEFAULT_BASE_URL,
    PROVIDERS,
    SEARCH_ENGINES,
    SUPPORTED_LANGUAGES,
    TRIM_SIZES,
    AppConfig,
    LLMConfig,
    ModelRoles,
    ProjectSettings,
    embedding_profile,
    language_tag,
    load_global_config,
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
from bookwriter.forge import PHASES, open_forge, phase_title
from bookwriter.forge import interview as iv
from bookwriter.forge import notebook as nb
from bookwriter.forge import questions as fg_questions
from bookwriter.forge import research_store as rstore
from bookwriter.forge import saves as fg_saves
from bookwriter.forge.production import trim_of
from bookwriter.forge.writing import MATTER
from bookwriter.gui.chat import ChatPanel, muted_color
from bookwriter.gui import phase_guide as pg
from bookwriter.gui.i18n import UI_LANGUAGES, current_language, set_language, t
from bookwriter.gui.research_view import ResearchExplorer
from bookwriter.gui.review_view import ReviewPage
from bookwriter.gui.servers_dialog import (
    ServerDialog,
    reasoning_choices,
    reasoning_tooltip,
    structured_choices,
    structured_tooltip,
)
from bookwriter.llm import servers as llm_servers
from bookwriter.gui.worker import INTERROTTO, Worker
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


class _BottomRow(QWidget):
    """La zona dei pulsanti: generali a sinistra, comandi di fase a destra.

    Se i due gruppi non ci stanno affiancati il gruppo di fase sale su una riga sua, sempre
    allineato a destra: i due gruppi non si mescolano mai.
    """

    def __init__(self, generali: QWidget, fase: QWidget) -> None:
        super().__init__()
        self._generali, self._fase = generali, fase
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._narrow: bool | None = None
        self._place(False)

    def _place(self, narrow: bool) -> None:
        if narrow == self._narrow:
            return
        self._narrow = narrow
        for w in (self._generali, self._fase):
            self._grid.removeWidget(w)
        self._grid.setColumnStretch(0, 0)
        self._grid.setColumnStretch(1, 1)
        if narrow:
            self._grid.addWidget(self._fase, 0, 0, 1, 2, Qt.AlignmentFlag.AlignRight)
            self._grid.addWidget(self._generali, 1, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
        else:
            self._grid.addWidget(self._generali, 0, 0, Qt.AlignmentFlag.AlignLeft)
            self._grid.addWidget(self._fase, 0, 2, Qt.AlignmentFlag.AlignRight)

    def resizeEvent(self, event) -> None:  # noqa: N802 - firma di Qt
        super().resizeEvent(event)
        bisogno = self._generali.sizeHint().width() + self._fase.sizeHint().width() + 24
        self._place(bisogno > self.width())

    def refit(self) -> None:
        """Il gruppo di fase cambia larghezza col pannello: si ricalcola dove sta."""
        bisogno = self._generali.sizeHint().width() + self._fase.sizeHint().width() + 24
        self._place(bisogno > self.width())


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
        # Chi ascolta l'avanzamento del lavoro in corso (la scheda «Scrivi da zero» lo mostra
        # nel riquadro di stato). Si imposta e si toglie con il lavoro, vedi ``_forge_job``.
        self._bg_hook: Callable[[int, int], None] | None = None
        self._fg_activity: dict | None = None
        #: il server dei modelli non ha le risorse per rispondere e il lavoro aspetta:
        #: ``(errore, secondi al prossimo tentativo)``, o ``None``. Vedi ``_on_model_wait``.
        self._fg_wait: tuple | None = None
        self._fg_job_seq = 0
        self._fg_error = ""
        self._fg_stream: tuple[int, int] | None = None  # (caratteri di ragionamento, di risposta)
        self._fg_warning = ""  # un'avvertenza da tenere nel riquadro (es. composto senza copertina)
        self._fg_research_stopped = ""  # il libro la cui ricerca è stata interrotta dall'autore
        self._fg_timer = QTimer(self)
        self._fg_timer.setInterval(1000)
        self._fg_timer.timeout.connect(self._forge_status_tick)
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
        self._auto_start_browser_if_enabled()

    def _auto_start_browser_if_enabled(self) -> None:
        if "PYTEST_CURRENT_TEST" in os.environ:
            return
        try:
            cfg = self.pm.active_config() if self.pm.active_slug() else load_global_config()
            target_url = cfg.research.external_fetcher_url or "http://127.0.0.1:8191/v1"
            if cfg.research.auto_start_browser and "127.0.0.1" in target_url:
                import threading
                from bookwriter.forge import browser_service
                threading.Thread(target=browser_service.start_trawl, kwargs={"wait_ready": False}, daemon=True).start()
        except Exception:
            pass


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
            (self._tab_authoring(), t("Scrivi da zero"), self._refresh_forge, True),
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
            self._busy_notice()
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
        worker.busy = busy  # per dire, a chi ne avvia un altro, quale operazione c'è già
        worker.stoppable = stoppable
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
            if self._bg_hook is not None:
                self._bg_hook(done, total)

        worker.signals.finished.connect(_finished)
        worker.signals.error.connect(_error)
        worker.signals.progress.connect(_prog)
        if on_note is not None:
            worker.signals.note.connect(on_note)
        self.pool.start(worker)

    def _running_operation(self) -> str:
        """Cosa sta facendo l'app adesso, in parole: la frase del riquadro di stato se c'è, altrimenti
        il nome del lavoro di sfondo. Vuoto se non sta facendo niente."""
        if self._fg_activity is not None:
            return str(self._fg_activity["text"]).rstrip("….")
        lavori = sorted(self._workers, key=lambda w: not getattr(w, "progressable", False))
        return t(getattr(lavori[0], "busy", "")).rstrip("….") if lavori else ""

    def _busy_notice(self) -> None:
        """«C'è già un'operazione in corso»: dice **quale**, e se si può fermare lo offre."""
        descrizione = self._running_operation() or t("un'altra operazione")
        fermabile = (not self._stop_requested
                     and any(getattr(w, "stoppable", False) for w in self._workers))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(t("Operazione in corso"))
        box.setText(t("C'è già un'operazione in corso: {0}.\n\nAspetta che finisca prima di "
                      "avviarne un'altra, oppure fermala (quello che ha già fatto resta "
                      "salvato).").format(descrizione))
        box.addButton(t("Aspetta"), QMessageBox.ButtonRole.AcceptRole)
        ferma = (box.addButton(t("Ferma l'operazione in corso"),
                               QMessageBox.ButtonRole.DestructiveRole) if fermabile else None)
        box.exec()
        if ferma is not None and box.clickedButton() is ferma:
            self._request_stop()

    def _label_stop_button(self) -> None:
        self.stop_button.setText(t("Interrompi"))
        self.stop_button.setToolTip(
            t("Ferma il lavoro alla fine del brano in corso. Quello già elaborato resta salvato: "
              "riavviando la stessa operazione si riprende da lì.")
        )

    def _request_stop(self) -> None:
        """Chiede al lavoro in corso di fermarsi appena finito il brano che ha per le mani."""
        self._stop_requested = True
        if self._fg_activity is not None:
            # Nella scheda «Scrivi da zero» «Interrompi» ferma davvero la chiamata in corso: si
            # chiude la connessione, non si aspetta che il modello finisca (un modello che
            # ragiona può metterci minuti). Gli altri lavori si fermano alla fine del brano.
            for cliente, _db in [*self._usage_clients, *self._live_clients]:
                cliente.cancel()
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
        # Cosa fa il modello mentre lavora (ragionamento in corso, risposta che arriva): il
        # riquadro di stato della scheda «Scrivi da zero» lo mostra.
        client.activity_hook = self._on_model_activity
        client.wait_hook = self._on_model_wait
        self._live_clients.append((client, self.pm.paths(slug).db))
        return client

    def _on_model_wait(self, errore, secondi: int) -> None:
        """Dal thread del lavoro: il server non ha le risorse per rispondere e si aspetta.
        Si ricorda soltanto; il riquadro di stato legge ogni secondo."""
        self._fg_wait = None if errore is None else (errore, secondi)

    def _on_model_activity(self, reasoning_chars: int, content_chars: int) -> None:
        """Chiamata dal thread del lavoro: si ricorda soltanto, la scheda legge ogni secondo."""
        self._fg_stream = (reasoning_chars, content_chars)

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

    def _format_combo(self, *, epub: bool = True) -> QComboBox:
        """Tendina del formato di uscita, uguale in ogni schermata che produce un file.

        L'EPUB manca dove il file serve a **rileggere le modifiche**: un libro elettronico non
        ha pagine né marcature di revisione, e offrirlo lì prometterebbe una cosa che non fa.
        """
        combo = QComboBox()
        combo.addItem(t("DOCX (Word)"), "docx")
        combo.addItem(t("PDF"), "pdf")
        combo.addItem(t("Markdown"), "md")
        if epub:
            combo.addItem(t("EPUB (libro elettronico)"), "epub")
        combo.setToolTip(
            t("In che formato salvare il libro finito. In tutti e tre l'impaginazione "
              "dell'originale viene conservata: titoli, capitoli, stacchi di pagina, corsivi.")
            if not epub else
            t("In che formato salvare il libro finito. DOCX, PDF e Markdown conservano "
              "l'impaginazione dell'originale: titoli, capitoli, stacchi di pagina, corsivi. "
              "L'EPUB no, e non può: un libro elettronico non ha pagine — carattere e misura "
              "li sceglie chi legge.")
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

    def closeEvent(self, event) -> None:  # noqa: N802 — nome imposto da Qt
        """Chiudere la finestra deve fermare il lavoro, non lasciarlo correre di nascosto.

        I lavori girano in un thread pool: senza questo, chiudendo la finestra il processo
        **resta vivo e invisibile** finché la traduzione non finisce. Chi riapre l'applicazione
        ne avvia una seconda, e i due processi si contendono lo stesso modello dimezzandosi la
        velocità a vicenda — è successo, e nei tempi dei blocchi si vede: un'ora e mezza per
        quattro blocchi mentre due traduzioni si alternavano sulla stessa scheda.

        Chi chiude decide: si ferma, oppure si resta. Quello che non si fa è chiudere gli occhi
        e lasciarlo lavorare.
        """
        # Solo i lavori lunghi vanno protetti. Una lettura dell'elenco modelli è anch'essa un
        # worker, ma interromperla non perde niente: chiedere conferma per quella sarebbe
        # fastidio senza motivo.
        lunghi = [w for w in self._workers if getattr(w, "progressable", False)]
        if not lunghi:
            try:
                from bookwriter.forge import browser_service
                browser_service.stop_trawl()
            except Exception:
                pass
            event.accept()
            return
        # Chi ha già premuto «Interrompi» ha già risposto: richiederglielo chiudendo la
        # finestra sarebbe fargli ripetere una decisione presa.
        if not self._stop_requested:
            scelta = QMessageBox.question(
                self, t("C'è un lavoro in corso"),
                t("Un'elaborazione è ancora in corso. Chiudendo adesso viene interrotta: quello "
                  "che ha già prodotto resta salvato e alla prossima apertura riprende da lì.\n\n"
                  "Chiudere e interrompere?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if scelta != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._stop_requested = True
        self._queue_stop = True
        event.accept()
        self._chiudi_davvero()

    #: Quanto si aspetta che il lavoro si fermi da sé, prima di chiudere comunque. La richiesta
    #: di interruzione viene raccolta **fra un passaggio e l'altro**, e un passaggio di
    #: traduzione dura minuti: aspettarne la fine terrebbe in vita un programma che l'utente ha
    #: già chiuso, e che lui non vede più.
    ATTESA_CHIUSURA_S = 20.0

    def _chiudi_davvero(self) -> None:
        """Fa terminare il processo, anche se un thread sta ancora macinando."""
        try:
            from bookwriter.forge import browser_service
            browser_service.stop_trawl()
        except Exception:
            pass
        QApplication.processEvents()  # lascia partire i segnali di interruzione già emessi
        if QThreadPool.globalInstance().waitForDone(int(self.ATTESA_CHIUSURA_S * 1000)):
            return  # si è fermato da solo: si esce per la via normale
        self._esci_a_forza()

    @staticmethod
    def _esci_a_forza() -> None:
        """Uscita immediata. Separata per poterla sostituire nei test, che non devono morire."""
        import os

        os._exit(0)

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
            # La scheda aperta mostra il progetto di prima finché non la si ridisegna: in «Scrivi
            # da zero» si scriverebbe al libro nuovo leggendo la conversazione del vecchio.
            self._on_tab_changed(self.tabs.currentIndex())

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
            write_output(
                compose_to_markdown(ppaths.db, oid), out, fmt,
                language=cfg.project.language or "it", production=cfg.production,
            )
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
        if formato in ("docx", "pdf", "md", "epub"):
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
    #  tab: scrivi da zero (le sette fasi)
    # ------------------------------------------------------------------ #
    #  Colore delle pastiglie di fase: fatta, in corso, da fare.
    _PHASE_STYLE: ClassVar[dict[str, str]] = {
        "fatta": "QPushButton{border:1px solid palette(mid);border-radius:9px;padding:5px 9px;"
                 "color:palette(window-text);background:palette(alternate-base)}",
        "in corso": "QPushButton{border:2px solid palette(highlight);border-radius:9px;"
                    "padding:4px 8px;font-weight:bold;color:palette(highlight)}",
        "da fare": "QPushButton{border:1px solid palette(midlight);border-radius:9px;"
                   "padding:5px 9px;color:palette(mid)}",
    }

    def _tab_authoring(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Parti da (quasi) niente: l'assistente ti intervista sul libro che hai in mente e "
              "poi lo fa, in sette fasi. Ogni capitolo viene salvato su disco appena è scritto — "
              "un libro dura più sessioni di lavoro, e quel che non è salvato non lo recupera "
              "nessuno.")
        ))

        # --- la barra delle fasi -----------------------------------------
        barra = QHBoxLayout()
        self.fg_phase_buttons: dict[str, QPushButton] = {}
        for indice, (pid, titolo) in enumerate(PHASES, 1):
            bottone = QPushButton(f"{indice} · {t(titolo)}")
            bottone.setFlat(True)
            bottone.setCursor(Qt.CursorShape.PointingHandCursor)
            bottone.clicked.connect(lambda _=False, p=pid: self._forge_go(p))
            self.fg_phase_buttons[pid] = bottone
            barra.addWidget(bottone)
        barra.addStretch(1)
        lay.addLayout(barra)

        # --- a che punto è la fase, e lo stile scelto ------------------------
        # Lo stile sta qui, fuori dai pannelli di fase, perché vale per tutto il libro: la
        # conversazione lo conosce (e non lo chiede), la specifica lo riporta, la stesura lo usa.
        dettaglio = QHBoxLayout()
        self.fg_detail = QLabel("")
        self.fg_detail.setWordWrap(True)
        self.fg_detail.setStyleSheet("color: palette(mid);")
        dettaglio.addWidget(self.fg_detail, 1)
        dettaglio.addWidget(QLabel(t("Stile:")))
        self.fg_style = QComboBox()
        self.fg_style.setMinimumWidth(220)
        self.fg_style.setToolTip(t("Lo stile del libro. L'assistente lo conosce e non te lo "
                                   "chiede: al più ti propone una precisazione."))
        self.fg_style.currentIndexChanged.connect(self._forge_style_changed)
        dettaglio.addWidget(self.fg_style)
        lay.addLayout(dettaglio)

        # --- al centro: la conversazione, o i resoconti delle altre fasi ------
        self.fg_center = QStackedWidget()
        self.fg_chat = ChatPanel()
        self.fg_chat.composer.submitted.connect(self._forge_send)
        self.fg_chat.composer.stop_requested.connect(self._request_stop)
        self.fg_center.addWidget(self.fg_chat)
        self.fg_log = QTextBrowser()
        self.fg_log.setOpenExternalLinks(True)
        self.fg_center.addWidget(self.fg_log)
        # Nella fase Ricerca il centro è l'esploratore: cosa si è trovato, punto per punto, e le
        # questioni aperte da decidere con l'assistente.
        self.fg_explorer = ResearchExplorer()
        self.fg_explorer.include_requested.connect(self._forge_include)
        self.fg_explorer.extend_requested.connect(self._forge_extend)
        self.fg_explorer.discard_requested.connect(self._forge_discard)
        self.fg_explorer.discard_old_requested.connect(self._forge_discard_old)
        self.fg_explorer.resume_requested.connect(self._forge_resume)
        self.fg_explorer.questions.composer.submitted.connect(self._questions_send)
        self.fg_explorer.questions.composer.stop_requested.connect(self._request_stop)
        self.fg_explorer.import_blocked_requested.connect(self._forge_import_blocked)
        self.fg_explorer.fetch_blocked_requested.connect(self._forge_fetch_blocked)
        self.fg_explorer.open_blocked_folder_requested.connect(self._forge_open_blocked_folder)
        self.fg_center.addWidget(self.fg_explorer)
        # Nella fase Revisione il centro è la pagina dei punti da discutere con l'assistente.
        self.fg_review = ReviewPage()
        self.fg_review.point_selected.connect(self._review_open)
        self.fg_review.skip_point_requested.connect(self._review_skip_point)
        self.fg_review.free_requested.connect(self._review_open_free)
        self.fg_review.chat.composer.submitted.connect(self._review_send)
        self.fg_review.chat.composer.stop_requested.connect(self._request_stop)
        self.fg_review.card.confirmed.connect(self._review_confirm)
        self.fg_review.card.dismissed.connect(self._review_dismiss)
        self.fg_center.addWidget(self.fg_review)
        self._rchat_busy = False
        self._review_key: tuple = ()
        self._qchat_busy = False
        self._qchat_key: tuple = ()
        lay.addWidget(self.fg_center, 1)
        self._chat_busy = False
        self._chat_queue: list[tuple[str, str]] = []  # (progetto, testo) in attesa di partire
        self._style_pending = False
        self._chat_slug = ""  # il libro di cui l'assistente sta parlando, mentre lavora
        self._chat_key: tuple = ()
        self._fg_shown_phase = ""

        # --- sotto: il riquadro di stato, i comandi generali a sinistra, quelli di fase a destra
        # Il riquadro sta sopra i pulsanti perché è la prima cosa da guardare: dice cosa sta
        # facendo l'app e cosa tocca all'autore (vedi ``_forge_status_update``).
        self.fg_cmds: dict[str, QPushButton] = {}
        self.fg_status_box = QFrame()
        self.fg_status_box.setObjectName("fg_status_box")
        self.fg_status_box.setStyleSheet(
            "#fg_status_box{background:palette(alternate-base);border:1px solid palette(mid);"
            "border-left:5px solid palette(highlight);border-radius:6px}")
        self.fg_status_box.setAccessibleName(t("Stato del lavoro"))
        casella = QVBoxLayout(self.fg_status_box)
        self.fg_doing = QLabel("")
        self.fg_doing.setObjectName("fg_doing")
        self.fg_doing.setWordWrap(True)
        self.fg_doing.setStyleSheet("font-weight: bold;")
        casella.addWidget(self.fg_doing)
        self.fg_todo = QLabel("")
        self.fg_todo.setObjectName("fg_todo")
        self.fg_todo.setWordWrap(True)
        casella.addWidget(self.fg_todo)
        azioni = QHBoxLayout()
        self.fg_status_settings = QPushButton(t("Accendi il web nelle Impostazioni"))
        self.fg_status_settings.setMinimumHeight(44)
        self.fg_status_settings.clicked.connect(self._forge_open_settings)
        azioni.addWidget(self.fg_status_settings)
        self.fg_status_resume = QPushButton(t("Riprendi la ricerca"))
        self.fg_status_resume.setMinimumHeight(44)
        self.fg_status_resume.setToolTip(t(
            "Continua da dove la ricerca si era fermata. Quello che è già salvato resta e non "
            "si rifà: si riparte dal punto rimasto a metà. È la scelta giusta dopo "
            "un'interruzione o un errore."))
        self.fg_status_resume.clicked.connect(lambda: self._forge_research_all(mode="resume"))
        azioni.addWidget(self.fg_status_resume)
        self.fg_status_redo = QPushButton(t("Rifai la ricerca"))
        self.fg_status_redo.setMinimumHeight(44)
        self.fg_status_redo.setToolTip(t(
            "Rifà il panorama e tutti i punti dei capitoli, tenendo quello che hai già: pagine "
            "lette, risultati, affermazioni e le tue decisioni restano, e si aggiunge quello "
            "di nuovo che si trova. Il panorama viene riscritto."))
        self.fg_status_redo.clicked.connect(lambda: self._forge_research_all(mode="redo"))
        azioni.addWidget(self.fg_status_redo)
        self.fg_status_fresh = QPushButton(t("Cancella e rifai"))
        self.fg_status_fresh.setMinimumHeight(44)
        self.fg_status_fresh.setToolTip(t(
            "Mette da parte tutto il materiale della ricerca (non lo cancella: va nella "
            "cartella «.superati» della ricerca) e ricomincia da zero, anche senza le tue "
            "decisioni sulle questioni."))
        self.fg_status_fresh.clicked.connect(lambda: self._forge_research_all(mode="fresh"))
        azioni.addWidget(self.fg_status_fresh)
        self.fg_status_questions = QPushButton(t("Apri “Questioni aperte”"))
        self.fg_status_questions.setMinimumHeight(44)
        self.fg_status_questions.clicked.connect(self._forge_open_questions)
        azioni.addWidget(self.fg_status_questions)
        azioni.addStretch(1)
        casella.addLayout(azioni)
        lay.addWidget(self.fg_status_box)

        # --- generali a sinistra, comandi di fase a destra: due gruppi che non si mescolano
        generali = QWidget()
        generali.setObjectName("fg_general_group")
        gl = QHBoxLayout(generali)
        gl.setContentsMargins(0, 0, 0, 0)
        stato = QPushButton(t("Stato su disco"))
        stato.setToolTip(t("Guarda i file che ci sono davvero, contro quelli che la specifica "
                           "prevede. È l'unico resoconto di cui fidarsi."))
        stato.clicked.connect(self._forge_status)
        gl.addWidget(stato)
        self.fg_read_spec = QPushButton(t("Leggi la specifica"))
        self.fg_read_spec.setToolTip(t("Mostra la specifica del libro, in sola lettura, in "
                                       "qualunque fase. Per modificarla serve la fase "
                                       "«Specifica»."))
        self.fg_read_spec.clicked.connect(self._forge_read_spec)
        gl.addWidget(self.fg_read_spec)
        cartella = QPushButton(t("Apri la cartella del libro"))
        cartella.clicked.connect(self._forge_open_folder)
        gl.addWidget(cartella)
        salva = QPushButton(t("Salva il lavoro"))
        salva.setToolTip(t("Mette da parte il libro com'è adesso, in un file solo che puoi "
                           "copiare altrove. Il lavoro prosegue da dove sei."))
        salva.clicked.connect(self._forge_save_work)
        gl.addWidget(salva)
        riprendi = QPushButton(t("Riprendi un salvataggio…"))
        riprendi.setToolTip(t("Rimette in lavorazione un libro messo da parte. Quello di adesso "
                              "viene salvato prima di essere sostituito."))
        riprendi.clicked.connect(self._forge_restore_save)
        gl.addWidget(riprendi)
        # Lo spazio non è estetica: «Ricomincia da capo» è l'unico comando che toglie qualcosa e
        # non deve stare a un pixel dai suoi vicini.
        gl.addSpacing(28)
        ricomincia = QPushButton(t("Ricomincia da capo"))
        ricomincia.setToolTip(t("Azzera le sette fasi e riparte dall'intervista. Prima chiede se "
                                "il libro di adesso va salvato o buttato."))
        ricomincia.clicked.connect(self._forge_reset)
        gl.addWidget(ricomincia)
        self.fg_saves_state = QLabel("")
        self.fg_saves_state.setStyleSheet("color: palette(mid);")
        gl.addWidget(self.fg_saves_state)
        for pulsante in generali.findChildren(QPushButton):
            pulsante.setMinimumHeight(44)

        fase = QWidget()
        fase.setObjectName("fg_phase_group")
        fl = QHBoxLayout(fase)
        fl.setContentsMargins(0, 0, 0, 0)
        self.fg_panels = QStackedWidget()
        for costruttore in (self._panel_interview, self._panel_spec, self._panel_research,
                            self._panel_review, self._panel_writing, self._panel_production, self._panel_build,
                            self._panel_handover):
            self.fg_panels.addWidget(costruttore())
        fl.addWidget(self.fg_panels)
        avanti = QPushButton(t("Passa alla specifica"))
        avanti.setObjectName("fg_advance")
        avanti.clicked.connect(self._forge_advance)
        self._forge_cmd("advance", avanti)
        fl.addWidget(avanti, 0, Qt.AlignmentFlag.AlignTop)
        self.fg_bottom = _BottomRow(generali, fase)
        lay.addWidget(self.fg_bottom)
        return w

    def _forge_cmd(self, key: str, button: QPushButton) -> QPushButton:
        """Registra un comando di fase: colorato secondo lo stato (vedi ``_forge_style_commands``)."""
        button.setMinimumHeight(44)
        button.setProperty("base_tip", button.toolTip())
        self.fg_cmds[key] = button
        return button

    # ---- i pannelli ------------------------------------------------------
    def _panel_interview(self) -> QWidget:
        w = QWidget()
        riga = QHBoxLayout(w)
        riga.setContentsMargins(0, 0, 0, 0)
        # Il testo d'aiuto lungo sta nel suggerimento: a dire cosa fare ora è il riquadro di stato.
        w.setToolTip(t("Racconta il libro come lo racconteresti a un editor: l'assistente "
                       "domanda, propone e discute con te. Stampa, copertina e formato si "
                       "decidono a parte, nella produzione."))
        self.fg_conversation = QPushButton(t("Torna alla conversazione"))
        self.fg_conversation.setMinimumHeight(44)
        self.fg_conversation.clicked.connect(lambda: self._forge_go("interview"))
        self.fg_conversation.setVisible(False)
        riga.addWidget(self.fg_conversation)
        self.fg_notebook_toggle = QPushButton(t("Quaderno del libro"))
        self.fg_notebook_toggle.setCheckable(True)
        self.fg_notebook_toggle.setChecked(True)
        self.fg_notebook_toggle.setMinimumHeight(44)
        self.fg_notebook_toggle.setToolTip(t("Mostra o nasconde il quaderno accanto alla "
                                             "conversazione."))
        self.fg_notebook_toggle.toggled.connect(self.fg_chat.show_notebook)
        riga.addWidget(self.fg_notebook_toggle)
        fine = QPushButton(t("Ho finito: decidi tu il resto"))
        fine.setMinimumHeight(44)
        fine.setToolTip(t("Chiude la conversazione. L'assistente completa il quaderno, scioglie "
                          "le questioni aperte e ti dice cosa ha deciso lui. Puoi sempre "
                          "riprendere a scrivere."))
        fine.clicked.connect(self._forge_finish_interview)
        self._forge_cmd("interview.finish", fine)
        riga.addWidget(fine)
        return w

    def _panel_spec(self) -> QWidget:
        w = QWidget()
        riga = QHBoxLayout(w)
        riga.setContentsMargins(0, 0, 0, 0)
        proponi = QPushButton(t("Proponi la struttura e scrivi la specifica"))
        proponi.clicked.connect(self._forge_make_spec)
        proponi.setToolTip(t("Se la specifica c'è già, la riscrive e toglie l'approvazione."))
        self._forge_cmd("spec.make", proponi)
        riga.addWidget(proponi)
        riga.addWidget(QLabel(t("capitoli:")))
        self.fg_chapters = QSpinBox()
        self.fg_chapters.setRange(0, 60)
        self.fg_chapters.setSpecialValueText(t("decidi tu"))
        self.fg_chapters.setToolTip(t("Zero lascia scegliere all'assistente quanti ne servono."))
        riga.addWidget(self.fg_chapters)
        modifica = QPushButton(t("Modifica la specifica…"))
        modifica.clicked.connect(self._forge_edit_spec)
        self._forge_cmd("spec.edit", modifica)
        riga.addWidget(modifica)
        approva = QPushButton(t("Approva la specifica"))
        approva.setToolTip(t("Da qui in poi si scrive contro questo documento. Rivedere una "
                             "struttura costa minuti, rivedere venti capitoli costa giorni."))
        approva.clicked.connect(self._forge_approve_spec)
        self._forge_cmd("spec.approve", approva)
        riga.addWidget(approva)
        return w

    def _panel_research(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        riga = QHBoxLayout()
        panorama = QPushButton(t("Guarda cosa esiste già"))
        panorama.setToolTip(t("Cerca opere simili al tuo libro: idee, immagini, scene e fonti da "
                              "cui attingere. Può ispirare la struttura, e per questo si fa "
                              "prima di fissarla."))
        panorama.clicked.connect(lambda: self._forge_landscape())
        self._forge_cmd("research.landscape", panorama)
        riga.addWidget(panorama)
        fonti = QPushButton(t("Cerca le fonti dei capitoli"))
        fonti.clicked.connect(self._forge_chapter_research)
        self._forge_cmd("research.chapters", fonti)
        riga.addWidget(fonti)
        rinuncia = QPushButton(t("Scrivi senza ricerca"))
        rinuncia.clicked.connect(self._forge_decline_research)
        self._forge_cmd("research.decline", rinuncia)
        riga.addWidget(rinuncia)
        lay.addLayout(riga)
        return w

    def _panel_review(self) -> QWidget:
        w = QWidget()
        riga = QHBoxLayout(w)
        riga.setContentsMargins(0, 0, 0, 0)
        w.setToolTip(t("La revisione è facoltativa: serve a discutere con l'assistente, un punto "
                       "alla volta, quello che la ricerca ha trovato. Saltarla non cambia niente."))
        prepara = QPushButton(t("Prepara i punti da discutere"))
        prepara.setToolTip(t("Raccoglie dalla ricerca tensioni, incoerenze e debolezze del libro. "
                             "Rifarlo non perde le decisioni già prese."))
        prepara.clicked.connect(self._review_generate)
        self._forge_cmd("review.generate", prepara)
        riga.addWidget(prepara)
        libera = QPushButton(t("Chat libera e perplessità"))
        libera.clicked.connect(self._review_open_free)
        self._forge_cmd("review.free", libera)
        riga.addWidget(libera)
        salta = QPushButton(t("Salta la revisione"))
        salta.setToolTip(t("Passa alla stesura. I punti lasciati restano da decidere e non "
                           "cambiano niente nel libro."))
        salta.clicked.connect(self._review_skip_all)
        self._forge_cmd("review.skip", salta)
        riga.addWidget(salta)
        return w

    def _panel_writing(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        riga = QHBoxLayout()
        uno = QPushButton(t("Scrivi il prossimo capitolo"))
        uno.clicked.connect(self._forge_write_next)
        self._forge_cmd("writing.next", uno)
        riga.addWidget(uno)
        tutti = QPushButton(t("Scrivi tutti i capitoli"))
        tutti.clicked.connect(self._forge_write_all)
        self._forge_cmd("writing.all", tutti)
        riga.addWidget(tutti)
        corti = QPushButton(t("Riscrivi i capitoli troppo corti"))
        corti.setToolTip(t("I capitoli scritti molto sotto il loro budget di parole vengono messi "
                           "da parte (non cancellati) e riscritti."))
        corti.clicked.connect(self._forge_redo_short)
        self._forge_cmd("writing.redo_short", corti)
        riga.addWidget(corti)
        lay.addLayout(riga)

        seconda = QHBoxLayout()
        seconda.addWidget(QLabel(t("Pagine iniziali e finali:")))
        self.fg_matter = QComboBox()
        for chiave in MATTER:
            self.fg_matter.addItem(t(chiave), chiave)
        seconda.addWidget(self.fg_matter)
        scrivi = QPushButton(t("Scrivi questa sezione"))
        scrivi.clicked.connect(self._forge_write_matter)
        self._forge_cmd("writing.matter", scrivi)
        seconda.addWidget(scrivi)
        lay.addLayout(seconda)
        return w

    def _panel_production(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        riga = QHBoxLayout()
        scegli = QPushButton(t("Scegli l'immagine di copertina…"))
        scegli.setToolTip(t("La copertina la fornisce l'autore: non viene inventata né generata "
                            "senza che tu l'abbia chiesto."))
        scegli.clicked.connect(self._forge_pick_cover)
        self._forge_cmd("production.cover", scegli)
        riga.addWidget(scegli)
        self.fg_cover = QLabel(t("nessuna copertina"))
        self.fg_cover.setWordWrap(True)
        riga.addWidget(self.fg_cover, 1)
        lay.addLayout(riga)

        seconda = QHBoxLayout()
        seconda.addWidget(QLabel(t("Misura della pagina:")))
        self.fg_trim = QComboBox()
        for chiave, etichetta in TRIM_SIZES:
            self.fg_trim.addItem(t(etichetta), chiave)
        seconda.addWidget(self.fg_trim, 1)
        scrivi = QPushButton(t("Scrivi book.yaml"))
        scrivi.setToolTip(t("Raccoglie in un file le decisioni prese nell'intervista: misura, "
                            "lingua, marchio, diritti, copertina, formati di uscita."))
        scrivi.clicked.connect(self._forge_produce)
        self._forge_cmd("production.write", scrivi)
        seconda.addWidget(scrivi)
        lay.addLayout(seconda)
        return w

    def _panel_build(self) -> QWidget:
        w = QWidget()
        riga = QHBoxLayout(w)
        riga.setContentsMargins(0, 0, 0, 0)
        riga.addWidget(QLabel(t("Formati:")))
        self.fg_pdf = QCheckBox(t("PDF di stampa"))
        self.fg_pdf.setChecked(True)
        self.fg_docx = QCheckBox(t("DOCX"))
        self.fg_docx.setChecked(True)
        self.fg_epub = QCheckBox(t("EPUB"))
        self.fg_epub.setChecked(True)
        self.fg_md = QCheckBox(t("Markdown (.md)"))
        self.fg_md.setChecked(True)
        for casella in (self.fg_pdf, self.fg_docx, self.fg_epub, self.fg_md):
            riga.addWidget(casella)
        self.fg_no_cover = QCheckBox(t("anche senza copertina"))
        self.fg_no_cover.setToolTip(t("Oltre alla versione con copertina, produce anche una copia "
                                      "senza copertina di ogni formato che ne porta una (PDF "
                                      "interno, EPUB). DOCX e Markdown non hanno mai copertina."))
        riga.addWidget(self.fg_no_cover)
        componi = QPushButton(t("Componi ed esporta"))
        componi.clicked.connect(self._forge_build)
        self._forge_cmd("build.build", componi)
        riga.addWidget(componi)
        return w

    def _panel_handover(self) -> QWidget:
        w = QWidget()
        riga = QHBoxLayout(w)
        riga.setContentsMargins(0, 0, 0, 0)
        controlli = QPushButton(t("Rifai i controlli di stampa"))
        controlli.clicked.connect(self._forge_check)
        self._forge_cmd("handover.check", controlli)
        riga.addWidget(controlli)
        note = QPushButton(t("Scrivi le note di produzione"))
        note.setToolTip(t("Elenca quel che resta aperto: ISBN, licenze dei caratteri, costola, "
                          "quarta di copertina, e quello che l'immagine non poteva dare."))
        note.clicked.connect(self._forge_handover)
        self._forge_cmd("handover.notes", note)
        riga.addWidget(note)
        return w

    # ---- infrastruttura della scheda --------------------------------------
    def _forge(self, *, with_client: bool = False):
        """Il libro del progetto attivo. ``None`` se non c'è un progetto."""
        slug = self.pm.active_slug()
        if not slug:
            QMessageBox.warning(self, t("Nessun progetto"), t("Crea o seleziona prima un progetto."))
            return None
        cfg = self.pm.config_for(slug)
        return open_forge(self.pm.paths(slug), cfg,
                          client=self._client(slug) if with_client else None)

    @staticmethod
    def _cover_label(controllo) -> str:
        """Lo stato della copertina, tradotto: è un'etichetta fissa, non un resoconto di passaggio.

        Il numero c'è sempre, anche quando la risposta è no: dire «non va bene» senza dire quanti
        pixel servono lascia l'autore senza niente da fare.
        """
        largo, alto = controllo.required
        if controllo.actual is None:
            return t("Nessuna copertina. Per la stampa ne serve una di almeno {0}×{1} pixel "
                     "(300 punti per pollice, abbondanza compresa).").format(largo, alto)
        testa = t("{0}: {1}×{2} pixel, {3} punti per pollice alla misura scelta.").format(
            controllo.path.name if controllo.path else "", *controllo.actual,
            round(controllo.dpi))
        if controllo.ok:
            return testa + " " + t("Va bene per la stampa.")
        if any("proporzioni" in a for a in controllo.warnings):
            avviso = t("Le proporzioni non sono quelle della pagina: il compositore la porta a "
                       "tutta larghezza e mette una fascia sotto per le scritte, invece di "
                       "tagliarne i lati.")
        else:
            avviso = t("È più piccola del necessario: servono {0}×{1} pixel. Si può ingrandire, "
                       "ma ingrandire non crea dettaglio.").format(largo, alto)
        return f"{testa} ⚠ {avviso}"

    def _forge_say(self, role: str, text: str) -> None:
        """Aggiunge una battuta alla conversazione e scorre in fondo."""
        etichetta = t("Assistente") if role == "assistant" else t("Tu")
        corrente = self.fg_log.toMarkdown() if self.fg_log.document().blockCount() > 1 else ""
        self.fg_log.setMarkdown(f"{corrente}\n\n**{etichetta}** — {text}\n".lstrip())
        barra = self.fg_log.verticalScrollBar()
        barra.setValue(barra.maximum())

    def _forge_show(self, text: str) -> None:
        """Mostra un risultato (uno stato, un resoconto) senza farlo passare per una battuta."""
        self.fg_log.setMarkdown(text)
        self._forge_center(log=True)

    def _forge_center(self, *, log: bool) -> None:
        """Al centro la conversazione o i resoconti. Durante l'intervista, un resoconto aperto
        lascia a portata di mano il modo di tornare a parlare."""
        if log and self._fg_shown_phase == "research":
            self.fg_center.setCurrentWidget(self.fg_explorer)
            self.fg_conversation.setVisible(False)
            return
        if log and self._fg_shown_phase == "review":
            self.fg_center.setCurrentWidget(self.fg_review)
            self.fg_conversation.setVisible(False)
            return
        self.fg_center.setCurrentWidget(self.fg_log if log else self.fg_chat)
        self.fg_conversation.setVisible(log)

    def _refresh_forge(self) -> None:
        """Rilegge dal disco a che punto è il libro e adegua la scheda."""
        forge = self._forge()
        if forge is None:
            return
        self._forge_paint(forge)
        self._forge_fill_styles(forge)
        self._forge_load_chat(forge)

    def _forge_fill_styles(self, forge) -> None:
        """Riempie la tendina degli stili e rimette quello scelto per questo libro."""
        self.fg_style.blockSignals(True)
        try:
            self.fg_style.clear()
            self.fg_style.addItem(t("— prosa chiara —"), None)
            for r in project_schema.list_styles(self.pm.paths(self.pm.active_slug()).db):
                self.fg_style.addItem(t("{0} ({1})").format(r["name"], r["origin"]), r["id"])
            indice = self.fg_style.findData(forge.state.style_id) if forge.state.style_id else 0
            self.fg_style.setCurrentIndex(max(0, indice))
        finally:
            self.fg_style.blockSignals(False)

    def _forge_style_changed(self, _indice: int) -> None:
        """Lo stile scelto si ricorda nel libro, se il libro c'è già; altrimenti con la prima
        battuta, che è quando il libro comincia. Se l'assistente sta lavorando si aspetta che
        finisca: salvare adesso finirebbe sotto lo stato che il suo lavoro salverà."""
        if self._chat_busy:
            self._style_pending = True
            return
        self._style_pending = False
        forge = self._forge()
        if forge is None or not forge.project.state_path.exists():
            return
        forge.set_style(self.fg_style.currentData())

    def _forge_load_chat(self, forge, *, force: bool = False) -> None:
        """Ridisegna la conversazione dal disco, se è cambiata da quella che si vede."""
        if self._chat_busy and not force:
            return
        chiave = (self.pm.active_slug(), forge.state.updated_at, len(forge.state.transcript))
        if chiave == self._chat_key and not force:
            return
        self._chat_key = chiave
        vista = self.fg_chat.view
        vista.clear()
        if not forge.state.transcript:
            vista.add_message("assistant", t(iv.OPENING))
        for battuta in forge.state.transcript:
            vista.add_message("user" if battuta.get("role") == "user" else "assistant",
                              battuta.get("text", ""))
        self.fg_chat.set_notebook(nb.render(forge.state.notebook))
        self.fg_chat.set_notebook_status("")
        vista.scroll_to_end()

    def _forge_paint(self, forge) -> None:
        """Colora le pastiglie, apre il pannello della fase corrente, aggiorna le etichette."""
        fasi = forge.phases()
        for info in fasi:
            bottone = self.fg_phase_buttons.get(info.id)
            if bottone is None:
                continue
            bottone.setStyleSheet(self._PHASE_STYLE.get(info.state, ""))
            bottone.setToolTip(f"{t(info.state)} — {info.detail}")
        corrente = next((i for i, f in enumerate(fasi) if f.id == forge.state.phase), 0)
        self.fg_panels.setCurrentIndex(corrente)
        # Lo stack prende la misura della pagina corrente, non della più larga: altrimenti il
        # gruppo di fase si allarga per le pagine che non si vedono.
        for i in range(self.fg_panels.count()):
            self.fg_panels.widget(i).setSizePolicy(
                QSizePolicy.Policy.Preferred if i == corrente else QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred if i == corrente else QSizePolicy.Policy.Ignored)
        self.fg_panels.updateGeometry()
        self.fg_bottom.refit()
        self.fg_detail.setText(fasi[corrente].detail)
        # Il centro cambia solo quando cambia la fase: un resoconto appena mostrato (un
        # salvataggio, lo stato su disco) non deve sparire perché qualcosa ha ridipinto la scheda.
        if forge.state.phase != self._fg_shown_phase:
            self._fg_shown_phase = forge.state.phase
            if forge.state.phase == "spec" and forge.project.spec_path.exists():
                # La specifica si mostrava solo subito dopo averla scritta: rientrando nella
                # fase (o riaprendo il programma) il centro era vuoto o mostrava altro.
                self.fg_log.setMarkdown(forge.project.spec_path.read_text(encoding="utf-8"))
            self._forge_center(log=forge.state.phase != "interview")
        self.fg_read_spec.setEnabled(forge.project.spec_path.exists())

        facts = pg.read_facts(forge)
        self._forge_style_commands(forge, facts)
        self._forge_status_update(forge, facts)
        if forge.state.phase == "research":
            self._forge_explorer_refresh(forge)
        if forge.state.phase == "review":
            self._review_refresh(forge)
        self.fg_cover.setText(self._cover_label(forge.cover_check()))
        # Quanti libri ci sono messi da parte: senza, «Riprendi un salvataggio…» è un pulsante
        # che non si sa se abbia qualcosa dietro finché non lo si preme.
        cartella = self._forge_saves_dir()
        quanti = len(fg_saves.list_saves(cartella)) if cartella else 0
        self.fg_saves_state.setText(
            "" if not quanti else
            t("1 salvataggio") if quanti == 1 else t("{0} salvataggi").format(quanti)
        )
        indice = self.fg_trim.findData(trim_of(forge.project, fallback=forge.cfg.production.trim))
        if indice >= 0:
            self.fg_trim.setCurrentIndex(indice)

    # ---- il riquadro di stato e i comandi di fase ---------------------------------
    def _forge_job(
        self,
        fn: Callable,
        on_done: Callable,
        busy: str,
        *,
        activity: str,
        steps: str = "",
        kind: str = "plain",
        progressable: bool = False,
        on_error: Callable[[str], None] | None = None,
        on_note: Callable[[object], None] | None = None,
        stoppable: bool = False,
        quiet_error: bool = False,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        """Un lavoro di sfondo della scheda, con il riquadro di stato che lo racconta.

        Passa da ``_run_bg`` (che resta l'unico a lanciare i lavori e a proteggere dalle
        sovrapposizioni) e in più imposta l'attività all'avvio, la aggiorna con l'avanzamento e
        la toglie alla fine o all'errore. ``activity`` è la frase al gerundio; ``steps`` la sua
        variante con «{0} di {1}», usata quando arriva l'avanzamento. ``kind``: ``chat`` (l'autore
        può continuare a scrivere), ``research`` o ``plain``. ``quiet_error``: un errore che non
        è da mostrare nel riquadro (l'aggiornamento del quaderno, che si recupera da solo).
        """
        if progressable and self._workers:
            # Sovrapposto: ``_run_bg`` avvisa e non parte, quindi non c'è nessuna attività.
            self._run_bg(fn, on_done, busy, progressable=True, on_error=on_error,
                         on_note=on_note, stoppable=stoppable)
            return
        self._fg_job_seq += 1
        token = self._fg_job_seq
        # Se il riquadro sta già raccontando un lavoro (la ricerca che gira) uno breve che parte
        # adesso non lo scalza: altrimenti, finendo, toglieva l'attività e il riquadro diceva
        # «Fermo, in attesa di te» con la ricerca ancora viva.
        primario = self._fg_activity is None
        if primario:
            self._fg_error = ""
            self._fg_stream = None
            self._fg_activity = {
                "token": token, "text": activity, "steps": steps, "progress": None,
                "kind": "stoppable" if kind == "plain" and stoppable else kind,
                "started": time.monotonic(),
            }
            self._fg_timer.start()

        def mio() -> bool:
            return (primario and self._fg_activity is not None
                    and self._fg_activity["token"] == token)

        def termina() -> None:
            if mio():
                self._fg_activity = None
                self._bg_hook = None
                self._fg_timer.stop()

        def avanzamento(done: int, total: int) -> None:
            if mio():
                self._fg_activity["progress"] = (done, total)
                self._forge_status_tick()

        def fatto(risultato: object) -> None:
            termina()
            try:
                on_done(risultato)
            finally:
                self._forge_status_update()

        def fallito(messaggio: str) -> None:
            termina()
            if messaggio == INTERROTTO:
                # L'autore ha premuto «Interrompi»: non è un errore, e non si mostra come tale.
                if kind == "research":
                    self._fg_research_stopped = self.pm.active_slug()
                self._fg_warning = t("Interrotto: quello che era già salvato resta.")
                self._forge_status_update()
                if on_cancel is not None:
                    on_cancel()
                return
            if not quiet_error:
                self._fg_error = messaggio
            self._forge_status_update()
            if on_error is not None:
                on_error(messaggio)
            else:
                QMessageBox.critical(self, t("Errore"), messaggio)

        if primario:
            self._bg_hook = avanzamento
        self._forge_status_update()
        self._run_bg(fn, fatto, busy, progressable=progressable, on_error=fallito,
                     on_note=on_note, stoppable=stoppable)

    def _forge_doing_text(self) -> str:
        """Cosa sta facendo l'app adesso, con i secondi trascorsi."""
        lavoro = self._fg_activity
        if lavoro is None:
            if self._workers:
                # Un lavoro è vivo anche se il riquadro non lo conosce: mai «fermo» in quel caso.
                return "● " + t("{0} (operazione in corso)").format(
                    self._running_operation() or t("Elaborazione"))
            return t("Fermo, in attesa di te.")
        testo = lavoro["text"]
        if lavoro["steps"] and lavoro["progress"] and lavoro["progress"][1] > 0:
            fatti, totale = lavoro["progress"]
            testo = lavoro["steps"].format(min(fatti + 1, totale), totale)
        if lavoro.get("detail"):
            testo = t("{0} · {1}").format(testo, lavoro["detail"])
        attesa = self._fg_wait
        if attesa is not None:
            errore, secondi = attesa
            if errore.kind == "ram" and errore.free_mib is not None:
                motivo = t("il server dei modelli ha poca RAM libera ({0} MiB, sotto la sua "
                           "soglia di {1} MiB)").format(f"{errore.free_mib:,}".replace(",", "."),
                                                        f"{errore.guard_mib:,}".replace(",", "."))
            else:
                motivo = t("il server dei modelli non può rispondere adesso ({0})").format(
                    errore.detail)
            testo = t("{0} · {1}").format(
                testo, t("In attesa: {0}; riprovo tra {1} s").format(motivo, secondi))
        flusso = self._fg_stream if attesa is None else None
        if flusso is not None:
            ragionamento, risposta = flusso
            if risposta > 0:
                testo = t("{0} · {1}").format(testo, t("il modello sta scrivendo la risposta…"))
            elif ragionamento > 0:
                testo = t("{0} · {1}").format(testo, t("il modello sta ragionando… {0} token")
                                              .format(f"{ragionamento // 4:,}".replace(",", ".")))
        secondi = int(time.monotonic() - lavoro["started"])
        return "● " + t("{0} ({1})").format(testo, pg.elapsed_text(secondi))

    def _forge_status_tick(self) -> None:
        """Ogni secondo: i secondi del lavoro in corso, senza rileggere il disco."""
        if hasattr(self, "fg_doing"):
            self.fg_doing.setText(self._forge_doing_text())

    def _forge_status_update(self, forge=None, facts=None) -> None:
        """Ricalcola le due righe del riquadro: cosa sta facendo l'app, cosa tocca all'autore."""
        if not hasattr(self, "fg_doing"):
            return
        if forge is None:
            slug = self.pm.active_slug()
            forge = self._forge_for(slug) if slug else None
        if forge is None:
            self.fg_doing.setText(self._forge_doing_text())
            self.fg_todo.setText(t("Crea o seleziona prima un progetto."))
            self.fg_status_settings.setVisible(False)
            self.fg_status_redo.setVisible(False)
            self.fg_status_questions.setVisible(False)
            return
        facts = facts or pg.read_facts(forge)
        lavoro = self._fg_activity
        web = self._forge_web_on()
        attivita = lavoro["kind"] if lavoro else ("plain" if self._workers else "")
        todo = pg.next_step(forge, web_on=web, activity_kind=attivita, facts=facts)
        if self._fg_warning:
            todo = self._fg_warning + "\n" + todo
        if self._fg_error:
            todo = t("Qualcosa non è andato: {0}\nPuoi riprovare: quello che era già salvato "
                     "resta.").format(self._fg_error) + "\n" + todo
        self.fg_doing.setText(self._forge_doing_text())
        self.fg_todo.setText(todo)
        self.fg_status_box.setAccessibleDescription(f"{self.fg_doing.text()} {todo}")
        in_ricerca = facts.phase == "research"
        self.fg_status_settings.setVisible(in_ricerca and not web)
        iniziata = facts.research_done or facts.landscape_done or facts.chapters_sourced
        ferma = lavoro is None and not self._workers
        scelte = in_ricerca and ferma and (iniziata or bool(self._fg_error))
        incompleta = not facts.research_done or facts.research_old_format
        self.fg_status_resume.setVisible(scelte and incompleta)
        self.fg_status_redo.setVisible(scelte)
        self.fg_status_fresh.setVisible(scelte)
        scuro = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        self.fg_status_resume.setStyleSheet(pg.command_qss(pg.CONSIGLIATO, scuro))
        self.fg_status_questions.setVisible(in_ricerca and facts.questions_pending > 0)

    def _forge_web_on(self) -> bool:
        slug = self.pm.active_slug()
        return bool(slug and self.pm.config_for(slug).research.enabled)

    def _forge_open_settings(self) -> None:
        """Porta alla scheda Impostazioni, dove si accende la ricerca sul web."""
        for indice, (aggiorna, _) in enumerate(self._tab_refreshers):
            if aggiorna == self._refresh_settings:
                self.tabs.setCurrentIndex(indice)
                return

    def _forge_style_commands(self, forge, facts=None) -> None:
        """Colora i comandi di fase secondo lo stato: consigliato, disponibile o superato."""
        stati = pg.command_states(forge, facts)
        scuro = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        parole = {pg.CONSIGLIATO: t("consigliato"), pg.DISPONIBILE: t("disponibile"),
                  pg.SUPERATO: t("superato")}
        for chiave, bottone in self.fg_cmds.items():
            cmd = stati.get(chiave)
            if cmd is None:
                continue
            if chiave == "advance":
                etichetta = pg.advance_label(forge.state.phase)
                bottone.setVisible(etichetta is not None)
                if etichetta:
                    bottone.setText(etichetta)
            bottone.setProperty("cmd_state", cmd.state)
            bottone.setStyleSheet(pg.command_qss(cmd.state, scuro))
            bottone.setEnabled(cmd.state != pg.SUPERATO)
            bottone.setToolTip(cmd.why if cmd.state == pg.SUPERATO
                               else str(bottone.property("base_tip") or ""))
            bottone.setAccessibleDescription(parole[cmd.state])

    def _chat_wait(self) -> bool:
        """Vero, e lo dice, se l'assistente sta lavorando sul libro.

        I comandi che salvano lo stato del libro aspettano: il lavoro in corso ne tiene una copia
        e, finendo, la salverebbe sopra la modifica appena fatta.
        """
        if not (self._chat_busy or self._qchat_busy or self._rchat_busy):
            return False
        QMessageBox.information(self, t("Un momento"),
                                t("Aspetta che l'assistente finisca di rispondere."))
        return True

    def _forge_go(self, phase: str) -> None:
        if self._chat_wait():
            return
        forge = self._forge()
        if forge is None:
            return
        forge.go_to(phase)
        self._fg_error = ""
        self._fg_warning = ""
        self._fg_shown_phase = ""  # chi preme una fase vuole vederla, anche se è quella di prima
        self._forge_paint(forge)
        self._forge_autostart_research(forge)

    def _forge_advance(self) -> None:
        if self._chat_wait():
            return
        forge = self._forge()
        if forge is None:
            return
        passato, messaggio = forge.advance()
        if not passato:
            scelta = QMessageBox.question(
                self, t("Manca qualcosa"),
                t("Per chiudere questa fase manca:\n\n{0}\n\nVuoi passare avanti lo stesso?")
                .format(messaggio),
            )
            if scelta != QMessageBox.StandardButton.Yes:
                return
            forge.advance(force=True)
        self._fg_error = ""
        self._forge_paint(forge)
        self._forge_autostart_research(forge)

    def _forge_status(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        self._forge_show("```\n" + forge.status().render() + "\n```")

    def _forge_open_folder(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        forge.project.scaffold()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(forge.project.root)))

    # ---- mettere da parte, riprendere, ricominciare -------------------------
    def _forge_saves_dir(self) -> Path | None:
        slug = self.pm.active_slug()
        return self.pm.paths(slug).saves if slug else None

    def _forge_save_work(self) -> None:
        """Mette da parte il libro com'è. Il lavoro prosegue da dove sta: non è un'uscita."""
        forge = self._forge()
        cartella = self._forge_saves_dir()
        if forge is None or cartella is None:
            return
        if not fg_saves.has_work(forge.project):
            QMessageBox.information(self, t("Niente da salvare"),
                                    t("Questo libro è ancora vuoto: comincia l'intervista."))
            return
        proposta = forge.status().title or t("libro in lavorazione")
        nome, ok = QInputDialog.getText(self, t("Salva il lavoro"),
                                        t("Come lo chiamiamo, questo salvataggio?"), text=proposta)
        if not ok:
            return

        def fatto(info) -> None:
            self._forge_show(t("Salvato: **{0}**\n\n{1}\n\n`{2}`").format(
                info.name, info.label(), info.path))
            self._forge_paint(forge)

        self._forge_job(lambda: fg_saves.save_work(forge.project, cartella, nome.strip()),
                        fatto, "Metto da parte il libro…",
                        activity=t("Sto mettendo da parte il libro…"))

    def _forge_restore_save(self) -> None:
        """Rimette in lavorazione un salvataggio. Quello di adesso viene messo da parte da sé."""
        if self._chat_wait():
            return
        forge = self._forge()
        cartella = self._forge_saves_dir()
        if forge is None or cartella is None:
            return
        elenco = fg_saves.list_saves(cartella)
        if not elenco:
            QMessageBox.information(
                self, t("Nessun salvataggio"),
                t("Non hai ancora messo da parte nessun libro. Il pulsante «Salva il lavoro» lo fa."))
            return
        etichette = [s.label() for s in elenco]
        scelta, ok = QInputDialog.getItem(self, t("Riprendi un salvataggio"),
                                          t("Quale rimetto in lavorazione?"), etichette, 0, False)
        if not ok:
            return
        salvataggio = elenco[etichette.index(scelta)]

        def fatto(info) -> None:
            self._forge_show(t("Ripreso: **{0}**\n\nQuello che stavi facendo è stato messo da "
                               "parte, lo trovi nell'elenco dei salvataggi.").format(info.name))
            self._refresh_forge()

        self._forge_job(
            lambda: fg_saves.restore_save(salvataggio.path, forge.project, backup_dir=cartella),
            fatto, "Riprendo il salvataggio…", activity=t("Sto riprendendo il salvataggio…"),
        )

    def _forge_reset_choice(self, forge) -> str:
        """Chiede cosa fare del libro di adesso: ``salva``, ``cancella`` o ``annulla``.

        Sta in un metodo suo perché la domanda è l'unica protezione che separa un ripensamento
        da una perdita: dev'essere una cosa sola, verificabile, e non sparsa nel gestore.
        """
        st = forge.status()
        cosa_si_perde = t("{0} capitoli, {1} parole, fase «{2}»").format(
            len(st.written), f"{st.words:,}".replace(",", "."),
            t(phase_title(forge.state.phase)))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("Ricominciare da capo"))
        box.setText(t("Stai per azzerare le sette fasi e ripartire dall'intervista."))
        box.setInformativeText(t(
            "Nel libro di adesso ci sono: {0}.\n\nPosso metterlo da parte prima — lo ritrovi con "
            "«Riprendi un salvataggio…» — oppure cancellarlo per sempre.\n\nI documenti importati "
            "e il resto del progetto non si toccano."
        ).format(cosa_si_perde))
        salva = box.addButton(t("Salva e ricomincia"), QMessageBox.ButtonRole.AcceptRole)
        cancella = box.addButton(t("Cancella e ricomincia"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(t("Annulla"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(salva)  # la via che non perde niente è quella preselezionata
        box.exec()
        premuto = box.clickedButton()
        if premuto is salva:
            return "salva"
        return "cancella" if premuto is cancella else "annulla"

    def _forge_reset(self) -> None:
        """Azzera il libro e riparte dall'intervista, dopo aver chiesto che fine fa quello vecchio."""
        if self._chat_wait():
            return
        self._chat_queue.clear()
        forge = self._forge()
        cartella = self._forge_saves_dir()
        if forge is None or cartella is None:
            return
        if not fg_saves.has_work(forge.project):
            fg_saves.clear_work(forge.project)  # cartelle vuote di un inizio mai partito
            self._refresh_forge()
            self._forge_center(log=False)
            return
        scelta = self._forge_reset_choice(forge)
        if scelta == "annulla":
            return
        messo_da_parte = None
        if scelta == "salva":
            try:
                messo_da_parte = fg_saves.save_work(
                    forge.project, cartella, forge.status().title or t("libro in lavorazione"))
            except fg_saves.SaveError as exc:
                # Se il salvataggio non riesce non si cancella niente: la richiesta era
                # «conservalo», e cancellare lo stesso sarebbe il contrario di quel che ha chiesto.
                QMessageBox.critical(self, t("Non ho potuto salvare"), t(
                    "Non sono riuscito a mettere da parte il libro, quindi non l'ho cancellato.\n\n{0}"
                ).format(str(exc)))
                return
        try:
            fg_saves.clear_work(forge.project)
        except fg_saves.SaveError as exc:
            QMessageBox.critical(self, t("Non ho potuto azzerare"), str(exc))
            return
        self._refresh_forge()
        if messo_da_parte is not None:
            self._forge_show(t("Ricominciato da capo. Il libro di prima è al sicuro: **{0}**\n\n"
                               "`{1}`").format(messo_da_parte.name, messo_da_parte.path))
        else:
            self._forge_show(t("Ricominciato da capo: racconta il nuovo libro all'assistente."))

    # ---- fase 1: la conversazione ----------------------------------------------
    #  Un'operazione alla volta: risposta, poi aggiornamento del quaderno. Due lavori sullo
    #  stesso libro insieme salverebbero due versioni diverse dello stato, e l'ultima
    #  cancellerebbe la prima. Quello che l'autore scrive nel frattempo si mette in fila e parte
    #  appena l'assistente è libero: si vede subito nella conversazione, non si perde.
    def _forge_send(self, testo: str) -> None:
        testo = testo.strip()
        if not testo:
            return
        if self._workers and not self._chat_busy:
            # Un'altra operazione lunga è in corso (una traduzione, la coda, una stesura): la
            # risposta non potrebbe partire, e sullo stesso libro i due lavori si
            # sovrascriverebbero lo stato. Il testo resta nel campo, pronto da mandare dopo.
            QMessageBox.information(
                self, t("Operazione in corso"),
                t("C'è un'altra operazione in corso. Il messaggio resta nel campo: mandalo "
                  "quando è finita."))
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return
        self.fg_chat.composer.clear()
        self.fg_chat.view.add_message("user", testo)
        self.fg_chat.view.scroll_to_end()
        if self._chat_busy:
            self._chat_queue.append((self.pm.active_slug(), testo))
            return
        self._forge_chat_reply(testo)

    def _forge_chat_reply(self, testo: str | None) -> None:
        """Manda la battuta all'assistente e fa scorrere la risposta mentre arriva.

        ``testo`` ``None`` vuol dire «riprova»: l'ultima battuta dell'autore è già salvata.
        """
        if self._workers:
            # _run_bg rifiuterebbe il lavoro senza richiamare nessuno, e la conversazione
            # resterebbe «occupata» per sempre. Succede con «Riprova» o con i messaggi in fila,
            # se nel frattempo è partita un'altra operazione: il testo torna nel campo.
            if testo:
                self.fg_chat.composer.input.setPlainText(
                    "\n\n".join(p for p in (testo, self.fg_chat.composer.text().strip()) if p))
            self.fg_chat.view.add_notice(t(
                "C'è un'altra operazione in corso: il messaggio non è partito ed è tornato nel "
                "campo di scrittura. Mandalo quando è finita."))
            return
        slug = self.pm.active_slug()
        forge = self._forge(with_client=True)
        if forge is None:
            return
        self._chat_slug = slug
        forge.state.style_id = int(self.fg_style.currentData() or 0)
        contratto = self._forge_style_contract(forge)
        apertura = t(iv.OPENING)
        bolla = self.fg_chat.view.begin_reply()
        self._chat_busy = True
        self.fg_chat.composer.set_streaming(True)

        def lavoro(_avanzamento, nota):
            def arriva(tipo: str, pezzo: str) -> None:
                if self._stop_requested:
                    raise iv.Stopped()
                nota((tipo, pezzo))

            try:
                return "fatto", forge.talk(testo, style_contract=contratto, opening=apertura,
                                           on_delta=arriva)
            except iv.Stopped:
                return "fermato", None

        def pezzo(nota) -> None:
            tipo, arrivato = nota
            if tipo == "content":
                bolla.append(arrivato)
            elif tipo == "reasoning":
                bolla.reasoning()
            elif tipo == "restart":
                bolla.reset()

        def fatto(esito) -> None:
            self.fg_chat.composer.set_streaming(False)
            stato, risposta = esito
            if stato == "fermato":
                bolla.finish(note=t("Risposta interrotta."))
            else:
                bolla.finish(risposta)
            if slug == self.pm.active_slug():
                self._chat_key = (slug, forge.state.updated_at, len(forge.state.transcript))
                self._forge_paint(forge)
            self._forge_update_notebook(slug)

        def fallito(messaggio: str) -> None:
            self.fg_chat.composer.set_streaming(False)
            bolla.finish(note=t("Nessuna risposta."))
            self.fg_chat.view.add_notice(
                t("La risposta non è arrivata: {0}\n\nQuello che hai scritto è salvato.")
                .format(messaggio),
                t("Riprova"), self._forge_chat_retry)
            self._chat_busy = False
            self._forge_chat_next()

        self._forge_job(lavoro, fatto, "L'assistente risponde…", progressable=True,
                        stoppable=True, on_error=fallito, on_note=pezzo,
                        activity=t("Sto preparando la risposta…"), kind="chat",
                        on_cancel=lambda: fatto(("fermato", None)))

    def _forge_chat_retry(self) -> None:
        if self._chat_busy:
            return
        self._forge_chat_reply(None)

    def _forge_for(self, slug: str, *, with_client: bool = False):
        """Il libro di un progetto preciso, che sia quello attivo o no."""
        cfg = self.pm.config_for(slug)
        return open_forge(self.pm.paths(slug), cfg,
                          client=self._client(slug) if with_client else None)

    def _forge_update_notebook(self, slug: str) -> None:
        """Dopo ogni risposta, il quaderno si mette al passo con la conversazione.

        ``slug`` è il libro della risposta, non quello attivo adesso: se nel frattempo si è
        passati a un altro progetto, il quaderno da aggiornare resta quello del libro di cui si
        stava parlando.
        """
        forge = self._forge_for(slug, with_client=True)
        self._chat_busy = True
        self.fg_chat.set_notebook_status(t("aggiorno…"))

        def fatto(esito) -> None:
            attivo = slug == self.pm.active_slug()
            if attivo:
                self.fg_chat.set_notebook(nb.render(forge.state.notebook))
                self.fg_chat.set_notebook_status("")
                self.fg_chat.notebook_status.setToolTip("")
                self._chat_key = (slug, forge.state.updated_at, len(forge.state.transcript))
                self._forge_paint(forge)
            self._chat_busy = False
            # L'autore ha chiesto di chiudere: la chat non avvia niente da sola, quindi lo fa
            # l'app. Solo sul libro aperto adesso (lo stile e la chat sono i suoi) e solo se la
            # conversazione non è già chiusa; se nel frattempo è partito un altro lavoro,
            # il pulsante «Ho finito» resta a disposizione.
            if (esito.close_requested and attivo and not forge.state.interview_closed
                    and not self._workers):
                self._forge_close_interview(automatic=True)
                return
            self._forge_chat_next()

        def fallito(messaggio: str) -> None:
            # Non è grave: il prossimo aggiornamento riprende anche questo tratto.
            self.fg_chat.set_notebook_status(t("non aggiornato, riprovo al prossimo scambio"))
            self.fg_chat.notebook_status.setToolTip(messaggio)
            self._chat_busy = False
            self._forge_chat_next()

        self._forge_job(forge.update_notebook, fatto, "Aggiorno il quaderno del libro…",
                        on_error=fallito, activity=t("Sto aggiornando il quaderno…"),
                        kind="chat", quiet_error=True)

    def _forge_chat_next(self) -> None:
        """L'assistente è libero: parte quello che l'autore ha scritto nel frattempo, tutto
        insieme, e si salva lo stile scelto mentre lavorava.

        Solo quello scritto per il libro aperto adesso: se nel frattempo si è passati a un altro
        progetto, quelle righe non sono per questo libro.
        """
        if self._chat_busy:
            return
        attivo = self.pm.active_slug()
        if self._chat_slug and self._chat_slug != attivo:
            # Si è cambiato progetto mentre l'assistente lavorava: adesso si può mostrare la
            # conversazione del libro aperto.
            self._chat_slug = ""
            forge = self._forge()
            if forge is not None:
                self._forge_load_chat(forge, force=True)
                self._forge_paint(forge)
        testi = [testo for progetto, testo in self._chat_queue if progetto == attivo]
        self._chat_queue.clear()
        if testi:
            self._forge_chat_reply("\n\n".join(testi))
        elif self._style_pending:
            self._forge_style_changed(self.fg_style.currentIndex())

    def _forge_finish_interview(self) -> None:
        """L'autore ha detto quello che gli premeva: il resto lo decide l'assistente."""
        if self._chat_wait():
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return
        if self._workers:
            self._busy_notice()
            return
        if not iv.exchanges(forge.state):
            scelta = QMessageBox.question(
                self, t("Decide tutto l'assistente?"),
                t("Non hai ancora detto niente del libro. Vuoi che l'assistente decida tutto "
                  "da sé?"))
            if scelta != QMessageBox.StandardButton.Yes:
                return
        self._forge_close_interview(automatic=False)

    def _forge_close_interview(self, *, automatic: bool) -> None:
        """La chiusura vera, dal pulsante o perché l'autore l'ha chiesta scrivendo.

        ``automatic``: niente domande, e la chat dice perché sta chiudendo.
        """
        forge = self._forge(with_client=True)
        if forge is None:
            return
        forge.state.style_id = int(self.fg_style.currentData() or 0)
        contratto = self._forge_style_contract(forge)
        slug = self.pm.active_slug()
        self._chat_slug = slug
        self._forge_center(log=False)
        bolla = self.fg_chat.view.begin_reply(
            t("Hai chiesto di cominciare: chiudo la conversazione e completo il quaderno…")
            if automatic else t("Completo il quaderno…"))
        self._chat_busy = True

        def fatto(messaggio: str) -> None:
            bolla.finish(messaggio)
            if slug == self.pm.active_slug():
                self.fg_chat.set_notebook(nb.render(forge.state.notebook))
                self._chat_key = (slug, forge.state.updated_at, len(forge.state.transcript))
                self._forge_paint(forge)
                # Il passo dopo si vede: senza, l'autore non sa che adesso tocca a lui.
                self.fg_chat.view.add_notice(
                    t("Conversazione chiusa e quaderno completo. Il passo successivo è la "
                      "specifica del libro."),
                    t("Passa alla specifica"), self._forge_advance)
                self.fg_chat.view.scroll_to_end()
            self._chat_busy = False
            self._forge_chat_next()

        def fallito(messaggio: str) -> None:
            bolla.finish(note=t("Il quaderno non è stato completato."))
            self.fg_chat.view.add_notice(
                t("Non sono riuscito a completare il quaderno: {0}").format(messaggio),
                t("Riprova"), self._forge_finish_interview)
            self._chat_busy = False
            self._forge_chat_next()

        self._forge_job(lambda: forge.finish_interview(style_contract=contratto), fatto,
                        "Completo il quaderno del libro…", on_error=fallito,
                        activity=t("Sto completando il quaderno…"))

    # ---- fase 2 -----------------------------------------------------------
    def _forge_make_spec(self) -> None:
        forge = self._forge(with_client=True)
        if forge is None:
            return
        quanti = self.fg_chapters.value() or None
        contratto = self._forge_style_contract(forge)

        def fatto(testo: str) -> None:
            self._forge_show(testo)
            self._forge_paint(forge)

        self._forge_job(lambda: forge.make_spec(chapters=quanti, style_contract=contratto), fatto,
                        "Progetto la struttura del libro…",
                        activity=t("Sto progettando la struttura del libro…"))

    def _forge_read_spec(self) -> None:
        """La specifica, da leggere in qualunque fase (anche dopo l'approvazione)."""
        forge = self._forge()
        if forge is None:
            return
        if not forge.project.spec_path.exists():
            QMessageBox.information(self, t("Niente da leggere"),
                                    t("La specifica non è ancora stata scritta."))
            return
        dialogo = QDialog(self)
        dialogo.setWindowTitle(t("Specifica del libro"))
        dialogo.resize(820, 640)
        lay = QVBoxLayout(dialogo)
        vista = QTextBrowser()
        vista.setOpenExternalLinks(True)
        vista.setMarkdown(forge.project.spec_path.read_text(encoding="utf-8"))
        lay.addWidget(vista)
        chiudi = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        chiudi.rejected.connect(dialogo.reject)
        chiudi.accepted.connect(dialogo.accept)
        lay.addWidget(chiudi)
        dialogo.exec()

    def _forge_edit_spec(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        if not forge.project.spec_path.exists():
            QMessageBox.information(self, t("Niente da modificare"),
                                    t("La specifica non è ancora stata scritta."))
            return
        testo = self._edit_text_dialog(
            t("Specifica del libro"), forge.project.spec_path.read_text(encoding="utf-8"))
        if testo is not None:
            forge.save_spec(testo)
            self._forge_show(testo)
            self._forge_paint(forge)

    def _edit_text_dialog(self, titolo: str, testo: str) -> str | None:
        """Finestra di modifica di un testo lungo. ``None`` se si annulla."""
        dialogo = QDialog(self)
        dialogo.setWindowTitle(titolo)
        dialogo.resize(820, 640)
        lay = QVBoxLayout(dialogo)
        editor = QPlainTextEdit(testo)
        lay.addWidget(editor)
        bottoni = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        bottoni.accepted.connect(dialogo.accept)
        bottoni.rejected.connect(dialogo.reject)
        lay.addWidget(bottoni)
        if dialogo.exec() != QDialog.DialogCode.Accepted:
            return None
        return editor.toPlainText()

    def _forge_approve_spec(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        if not forge.project.spec_path.exists():
            QMessageBox.information(self, t("Niente da approvare"),
                                    t("La specifica non è ancora stata scritta."))
            return
        forge.approve_spec()
        self._forge_say("assistant", t("Specifica approvata. Da qui in poi si scrive contro "
                                       "questo documento."))
        self._forge_paint(forge)
        self._forge_autostart_research(forge)

    # ---- fase 3 -----------------------------------------------------------
    def _forge_landscape(self, *, then: Callable[[], None] | None = None) -> None:
        """Guarda cosa esiste già. ``then`` parte a lavoro finito, se nessuno ha interrotto."""
        forge = self._forge(with_client=True)
        if forge is None:
            return

        def fatto(report) -> None:
            if report.stopped:
                self._fg_research_stopped = self.pm.active_slug()
                self._forge_show(t("Ricerca interrotta: il panorama non è stato scritto."))
            else:
                self._forge_show(report.path.read_text(encoding="utf-8") if report.path
                                 else report.summary())
            self._forge_paint(forge)
            if then is not None and not report.stopped and not self._stop_requested:
                then()

        self._forge_job(lambda p: forge.research_landscape(
                            progress=p, should_stop=lambda: self._stop_requested), fatto,
                        "Guardo cosa esiste già…", progressable=True, stoppable=True,
                        activity=t("Sto guardando cosa esiste già…"),
                        steps=t("Sto guardando cosa esiste già ({0} di {1})…"), kind="research")

    def _forge_chapter_research(self) -> None:
        forge = self._forge(with_client=True)
        if forge is None:
            return
        if not forge.state.outline:
            QMessageBox.information(self, t("Manca la struttura"),
                                    t("Prima serve la specifica con l'elenco dei capitoli."))
            return
        righe: list[str] = []

        def avanzamento(nota) -> None:
            if isinstance(nota, tuple):
                self._forge_note_detail(nota)
                return
            righe.append(str(nota))
            self._forge_show("\n\n".join(righe))

        def fatto(rapporti) -> None:
            self._forge_show("\n\n".join(righe) or t("Nessuna fonte raccolta."))
            if not forge.state.research_done:
                self._fg_research_stopped = self.pm.active_slug()
            self._forge_paint(forge)
            if forge.pending_questions() and not self._stop_requested:
                self.fg_explorer.show_questions()  # c'è da decidere: si va dritti lì

        self._forge_job(
            lambda p, n: forge.research_chapters(progress=p, note=n,
                                                 should_stop=lambda: self._stop_requested),
            fatto, "Cerco le fonti dei capitoli…", progressable=True, stoppable=True,
            on_note=avanzamento, activity=t("Sto cercando le fonti dei capitoli…"),
            steps=t("Sto cercando le fonti: capitolo {0} di {1}…"), kind="research",
        )

    def _forge_research_all(self, *, mode: str = "resume") -> None:
        """Tutta la ricerca in sequenza: il panorama, poi le fonti dei capitoli.

        Il panorama dipende dal quaderno e non dalla struttura: se c'è già (nel formato nuovo) si
        riusa e si passa alle fonti, a meno che non si chieda di rifare tutto (``redo``).

        Parte da sola quando si entra nella fase e la ricerca non è fatta né rinunciata, e col
        pulsante «Rifai la ricerca». È interrompibile: ``Interrompi`` ferma le fonti dei capitoli
        e impedisce che partano, se si ferma prima.
        """
        if self._chat_wait():
            return
        forge = self._forge()
        if forge is None:
            return
        if not forge.state.outline:
            QMessageBox.information(self, t("Manca la struttura"),
                                    t("Prima serve la specifica con l'elenco dei capitoli."))
            return
        if mode == "fresh":
            progresso = forge.research_progress()
            scelta = QMessageBox.question(
                self, t("Cancella e rifai"),
                t("Metto da parte tutto il materiale della ricerca (panorama, fonti, questioni "
                  "e le tue decisioni) e ricomincio da zero.\n\nI file non si cancellano: "
                  "restano nella cartella «.superati» dentro la cartella della ricerca. "
                  "Capitoli già completi: {0} di {1}.\n\nProcedo?").format(
                    progresso["done"], progresso["chapters"]),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if scelta != QMessageBox.StandardButton.Yes:
                return
            forge.set_aside_research()
        elif mode == "redo":
            forge.prepare_redo()
        self._fg_research_stopped = ""
        if mode == "resume" and forge.landscape_data() is not None:
            self._forge_chapter_research()
        else:
            self._forge_landscape(then=self._forge_chapter_research)

    def _forge_autostart_research(self, forge) -> None:
        """Entrando nella fase Ricerca con la ricerca da fare, la fa partire.

        La ricerca non chiede scelte all'autore (stile, formati, copertina sì, questa no): farla
        aspettare un clic è tempo perso. Con il web spento parte lo stesso, e il riquadro di stato
        lo dice. Non riparte se è fatta o rinunciata; le altre fasi non partono mai da sole.
        """
        st = forge.state
        if st.phase != "research" or st.research_declined:
            return
        vecchia = forge.research_format_old()
        if st.research_done and not vecchia:
            return
        if not st.outline or self._workers or self._chat_busy:
            return
        if self._fg_research_stopped == self.pm.active_slug():
            return  # l'autore l'ha interrotta: non si riparte da soli, c'è «Rifai la ricerca»
        if vecchia:
            # Una ricerca nel formato precedente (solo .md) non ha punti né questioni: si rifà
            # tutta, e da adesso non vale più come fatta.
            st.research_done = False
            forge.save()
        self._forge_research_all(mode="resume")

    # ---- l'esploratore e le questioni aperte --------------------------------------
    def _forge_note_detail(self, nota: tuple) -> None:
        """Una nota strutturata della ricerca: a che punto del capitolo si è, nel riquadro."""
        if self._fg_activity is not None:
            if nota[0] == "point":
                dettaglio = t("capitolo {0}, punto {1} di {2}").format(*nota[1:4])
            elif nota[0] == "section":
                dettaglio = t("sezione {0} di {1}").format(*nota[2:4])
            else:
                dettaglio = t("approfondisco una questione (giro {0} di {1})").format(*nota[2:4])
            self._fg_activity["detail"] = dettaglio
        self._forge_status_tick()

    def _forge_explorer_refresh(self, forge=None) -> None:
        """Rilegge dal disco la ricerca e ridisegna l'esploratore e le questioni."""
        if forge is None:
            slug = self.pm.active_slug()
            if not slug:
                return
            forge = self._forge_for(slug)
        capitoli = rstore.load_all_chapters(forge.project.research_dir, forge.state.outline)
        self.fg_explorer.set_data(forge.landscape_data(), forge.state.outline, capitoli,
                                  web_on=self._forge_web_on())
        self.fg_explorer.set_blocked_data(forge.blocked_pages())
        self.fg_explorer.set_questions_count(
            sum(1 for c in capitoli for q in c.questions if not q.closed))
        self._questions_load(forge)

    def _forge_import_blocked(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        esiti = forge.import_blocked_pages()
        importati = sum(1 for e in esiti if e.get("status") == "ok")
        if esiti:
            msg = t("Importate {0} pagine bloccate con successo.").format(importati)
            self.statusBar().showMessage(msg, 5000)
        else:
            self.statusBar().showMessage(t("Nessun file trovato in research/bloccate/ricevute/."), 4000)
        self._forge_explorer_refresh(forge)

    def _forge_fetch_blocked(self) -> None:
        """«Recupera le pagine in coda»: le chiede al servizio esterno e le importa."""
        if self._chat_wait():
            return
        if self._workers:
            self._busy_notice()
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return
        if not forge.cfg.research.external_fetcher_url.strip():
            QMessageBox.information(
                self, t("Servizio esterno non impostato"),
                t("Imposta l'indirizzo del servizio esterno in Impostazioni → Ricerca."))
            return

        def fatto(esito) -> None:
            self._forge_explorer_refresh(forge)
            self._forge_paint(forge)
            if not esito["total"]:
                testo = t("Nessuna pagina in coda da recuperare.")
            else:
                ancora = ", ".join(f"{n} {m}" for m, n in esito["still_blocked"].items()) or "0"
                testo = t("Recuperate {0} di {1}. Ancora bloccate: {2}. Non pertinenti: {3}. "
                          "Senza risposta dal servizio: {4}.").format(
                    esito["fetched"], esito["total"], ancora, esito["irrelevant"],
                    esito["no_answer"])
                if not esito["service_ready"]:
                    testo += " " + t("Il servizio esterno non risultava pronto.")
            QMessageBox.information(self, t("Pagine in coda"), testo)

        self._forge_job(
            lambda p: forge.fetch_blocked_pages(
                progress=p, should_stop=lambda: self._stop_requested),
            fatto, "Recupero le pagine in coda…", progressable=True, stoppable=True,
            activity=t("Sto recuperando le pagine in coda…"),
            steps=t("Sto recuperando le pagine in coda ({0} di {1})…"), kind="research")

    def _forge_open_blocked_folder(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        from bookwriter.forge import blocked
        cartella = blocked.ricevute_dir(forge.project.research_dir)
        cartella.mkdir(parents=True, exist_ok=True)
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(cartella.parent)))

    def _forge_discard(self, numero: int, tipo: str, ident: str, scarta: bool) -> None:
        """«Scarta» / «Ripristina» su una questione, un risultato o del materiale: non cancella
        niente dal disco."""
        if self._workers:
            self._busy_notice()  # la ricerca in corso riscrive lo stesso file del capitolo
            return
        forge = self._forge()
        if forge is None:
            return
        try:
            forge.discard_item(numero, tipo, ident, scarta)
        except ValueError as exc:
            QMessageBox.information(self, t("Niente da fare"), str(exc))
            return
        self._forge_explorer_refresh(forge)
        self._forge_paint(forge)

    def _forge_discard_old(self) -> None:
        if self._workers:
            self._busy_notice()
            return
        forge = self._forge()
        if forge is None:
            return
        quante = forge.discard_old_questions()
        self._qchat_key = ()
        self._forge_explorer_refresh(forge)
        self._forge_paint(forge)
        self.fg_explorer.questions.view.add_notice(
            t("Scartate {0} questioni vecchie. Non si è cancellato niente: le puoi ripristinare "
              "una per una dalla scheda del capitolo.").format(quante))

    def _forge_resume(self) -> None:
        if self._chat_wait():
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return

        def fatto(quante) -> None:
            self._forge_explorer_refresh(forge)
            self._forge_paint(forge)
            self.fg_explorer.questions.view.add_notice(
                t("Ripreso l'approfondimento di {0} questioni.").format(quante))

        self._forge_job(
            lambda p, n: forge.resume_deepening(note=n, should_stop=lambda: self._stop_requested),
            fatto, "Riprendo l'approfondimento…", progressable=True, stoppable=True,
            on_note=lambda n: self._forge_note_detail(n) if isinstance(n, tuple) else None,
            activity=t("Sto riprendendo l'approfondimento…"), kind="research")

    def _forge_open_questions(self) -> None:
        self.fg_center.setCurrentWidget(self.fg_explorer)
        self.fg_explorer.show_questions()

    def _forge_include(self, numero: int, punto: int, url: str) -> None:
        """«Includi nella ricerca»: legge la pagina e la aggiunge alle fonti di quel punto."""
        if self._chat_wait():
            return
        if self._workers:
            # La ricerca in corso riscrive lo stesso file del capitolo: due lavori insieme si
            # sovrascriverebbero (e, finendo, questo toglieva anche l'attività dal riquadro).
            self._busy_notice()
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return

        def fatto(_pagina) -> None:
            self._forge_explorer_refresh(forge)
            self._forge_paint(forge)

        # Un errore (pagina che non risponde, web spento) compare nel riquadro di stato.
        self._forge_job(lambda: forge.include_result(numero, punto, url), fatto,
                        "Leggo la pagina…", on_error=lambda _m: None,
                        activity=t("Sto leggendo la pagina…"))

    def _forge_extend(self, numero: int, punto: int) -> None:
        """«Estendi la ricerca»: nuove interrogazioni e più risultati per un punto."""
        if self._chat_wait():
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return

        def nota(n) -> None:
            if isinstance(n, tuple):
                self._forge_note_detail(n)

        def fatto(_nuovi) -> None:
            self._forge_explorer_refresh(forge)
            self._forge_paint(forge)

        self._forge_job(
            lambda p, n: forge.extend_point(numero, punto, note=n,
                                            should_stop=lambda: self._stop_requested),
            fatto, "Estendo la ricerca…", progressable=True, stoppable=True, on_note=nota,
            on_error=lambda _m: None, activity=t("Sto estendendo la ricerca…"), kind="research")

    # ---- la conversazione sulle questioni aperte ----------------------------------
    def _questions_intro(self, pendenti: list) -> str:
        if not pendenti:
            return t("Nessun contrasto da decidere: quello che hai scritto resta com'è.")
        testa = (t("La ricerca ha segnalato un contrasto fra quanto scrivi e un fatto che sembra "
                   "comprovato. Nel libro resta come l'hai scritto finché non decidi.")
                 if len(pendenti) == 1 else
                 t("La ricerca ha segnalato {0} contrasti fra quanto scrivi e fatti che sembrano "
                   "comprovati. Nel libro restano come li hai scritti finché non decidi.")
                 .format(len(pendenti)))
        web = self._forge_web_on()
        righe = [testa, ""]
        for q in pendenti:
            provate = (t("1 interrogazione") if q.tried() == 1
                       else t("{0} interrogazioni").format(q.tried()))
            viste = (t("1 risultato visto") if q.seen() == 1
                     else t("{0} risultati visti").format(q.seen()))
            righe.append("- " + (t("{0} (capitolo {1}): provato con {2}, {3}.").format(
                                     q.text, q.chapter, provate, viste)
                                 if q.attempts else
                                 t("{0} (capitolo {1}): non ho approfondito, la ricerca sul web è "
                                   "spenta nelle impostazioni.").format(q.text, q.chapter)
                                 if not web else
                                 t("{0} (capitolo {1}): non l'ho ancora approfondito, la ricerca "
                                   "si è fermata prima.").format(q.text, q.chapter)))
        righe += ["", t("Per ciascuno puoi incollare l'indirizzo di una fonte, allegare un "
                        "documento (pdf, docx, md, txt), dirmi come scriverlo — ti propongo io "
                        "una formulazione — oppure tralasciarlo. Da quale cominciamo?")]
        return "\n".join(righe)

    def _questions_markdown(self, domande: list) -> str:
        parole = {rstore.APERTA: t("da approfondire"), rstore.IN_APPROFONDIMENTO:
                  t("in approfondimento"), rstore.RISOLTA: t("risolta"),
                  rstore.CHIESTA: t("da decidere"), rstore.DECISA: t("decisa")}
        righe = []
        for q in domande:
            esito = q.outcome or {}
            riga = f"- **{parole.get(q.status, q.status)}** — " + t("capitolo {0}:").format(
                q.chapter) + f" {q.text}"
            if q.discarded:
                riga += " → " + t("scartata")
            elif esito.get("kind") == rstore.OUT_SOURCE:
                riga += " → " + t("fonte:") + f" {esito.get('source', '')}"
            elif esito.get("kind") == rstore.OUT_PUBLISH:
                riga += " → " + t("si pubblica così:") + f" {esito.get('text', '')}"
            elif esito.get("kind") == rstore.OUT_OMIT:
                riga += " → " + t("tralasciata")
            righe.append(riga)
        return "\n".join(righe)

    def _questions_key(self, forge, domande=None) -> tuple:
        domande = forge.questions() if domande is None else domande
        return (self.pm.active_slug(), len(forge.state.questions_transcript),
                tuple((q.id, q.status, q.discarded) for q in domande))

    def _questions_load(self, forge, *, force: bool = False) -> None:
        """Ridisegna la conversazione sulle questioni dal disco, se è cambiata."""
        if self._qchat_busy and not force:
            return
        domande = forge.questions()
        pendenti = [q for q in domande if not q.closed]
        chiave = self._questions_key(forge, domande)
        if chiave == self._qchat_key and not force:
            return
        self._qchat_key = chiave
        pannello = self.fg_explorer.questions
        pannello.view.clear()
        if not forge.state.questions_transcript:
            pannello.view.add_message("assistant", self._questions_intro(pendenti))
        for battuta in forge.state.questions_transcript:
            pannello.view.add_message("user" if battuta.get("role") == "user" else "assistant",
                                      battuta.get("text", ""))
        pannello.set_notebook(self._questions_markdown(domande))
        pannello.view.scroll_to_end()

    def _questions_send(self, testo: str) -> None:
        pannello = self.fg_explorer.questions
        testo = testo.strip()
        allegati = pannello.composer.take_attachments()
        if not testo and not allegati:
            return
        if self._qchat_busy or self._chat_busy:
            pannello.view.add_notice(t("Aspetta che l'assistente finisca di rispondere."))
            return
        if self._workers:
            self._busy_notice()
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return
        if not forge.state.questions_transcript:
            # L'apertura che si vede (le questioni elencate) fa parte della conversazione.
            forge.state.questions_transcript.append(
                {"role": "assistant", "text": self._questions_intro(forge.pending_questions())})
        mostrato = testo
        if allegati:
            mostrato += ("\n\n" if mostrato else "") + t("Allegati: {0}").format(
                ", ".join(p.name for p in allegati))
        pannello.composer.clear()
        pannello.view.add_message("user", mostrato)
        pannello.view.scroll_to_end()
        self._questions_reply(forge, testo or None, allegati)

    def _questions_reply(self, forge, testo: str | None, allegati: list) -> None:
        pannello = self.fg_explorer.questions
        slug = self.pm.active_slug()
        bolla = pannello.view.begin_reply()
        self._qchat_busy = True
        pannello.composer.set_streaming(True)

        def lavoro(_avanzamento, nota):
            def arriva(tipo: str, pezzo: str) -> None:
                if self._stop_requested:
                    raise iv.Stopped()
                nota((tipo, pezzo))

            try:
                return "fatto", forge.questions_talk(testo, allegati, on_delta=arriva)
            except iv.Stopped:
                return "fermato", None

        def pezzo(nota) -> None:
            tipo, arrivato = nota
            if tipo == "content":
                bolla.append(arrivato)
            elif tipo == "reasoning":
                bolla.reasoning()
            elif tipo == "restart":
                bolla.reset()

        def fatto(esito) -> None:
            pannello.composer.set_streaming(False)
            stato, risultato = esito
            if stato == "fermato":
                bolla.finish(note=t("Risposta interrotta."))
            else:
                risposta, risolte, materiale = risultato
                bolla.finish(risposta)
                if testo and fg_questions.urls_in(testo) and not self._forge_web_on():
                    pannello.view.add_notice(t(
                        "Non ho letto gli indirizzi: la ricerca sul web è spenta. Accendila in "
                        "Impostazioni, oppure allega la pagina come documento."))
                else:
                    for avviso in materiale.notes:
                        pannello.view.add_notice(avviso)
                if risolte:
                    pannello.view.add_notice(t("Risolte con il tuo materiale: {0}").format(
                        "; ".join(q.text for q in risolte)))
            self._questions_after(slug)

        def fallito(messaggio: str) -> None:
            pannello.composer.set_streaming(False)
            bolla.finish(note=t("Nessuna risposta."))
            pannello.view.add_notice(
                t("La risposta non è arrivata: {0}\n\nQuello che hai scritto è salvato.")
                .format(messaggio), t("Riprova"), self._questions_retry)
            self._qchat_busy = False

        def interrotto() -> None:
            pannello.composer.set_streaming(False)
            bolla.finish(note=t("Risposta interrotta."))
            self._qchat_busy = False

        self._forge_job(lavoro, fatto, "L'assistente risponde…", progressable=True,
                        stoppable=True, on_error=fallito, on_note=pezzo,
                        activity=t("Sto preparando la risposta…"), kind="chat",
                        on_cancel=interrotto)

    def _questions_retry(self) -> None:
        if self._qchat_busy:
            return
        forge = self._forge(with_client=True)
        if forge is not None:
            self._questions_reply(forge, None, [])

    def _questions_after(self, slug: str) -> None:
        """Dopo ogni risposta: si registra solo quello che l'autore ha deciso in modo esplicito."""
        forge = self._forge_for(slug, with_client=True)

        def fatto(registrate) -> None:
            self._qchat_busy = False
            if slug == self.pm.active_slug():
                if registrate:
                    self.fg_explorer.questions.view.add_notice(
                        t("Registrato: {0}").format("; ".join(q.text for q in registrate)))
                # La conversazione che si vede è già quella giusta, con i suoi avvisi: non si
                # ridisegna dal disco (li cancellerebbe). Si aggiorna solo l'elenco accanto.
                domande = forge.questions()
                self._qchat_key = self._questions_key(forge, domande)
                self.fg_explorer.questions.set_notebook(self._questions_markdown(domande))
                self._forge_explorer_refresh(forge)
                self._forge_paint(forge)

        def fallito(_messaggio: str) -> None:
            # Non è grave: il prossimo scambio riprende anche questo tratto.
            self._qchat_busy = False

        self._forge_job(forge.questions_update, fatto, "Registro le decisioni…",
                        on_error=fallito, activity=t("Sto registrando le decisioni…"),
                        kind="chat", quiet_error=True)

    # ---- la revisione dei risultati --------------------------------------------------
    def _review_refresh(self, forge, *, force: bool = False) -> None:
        """Rilegge dal disco l'elenco dei punti e, se è cambiato, il punto aperto."""
        if self._rchat_busy and not force:
            return
        dati = forge.review_data()
        pagina = self.fg_review
        chiave = (self.pm.active_slug(), pagina.free_mode, pagina.current_id,
                  tuple((p.id, p.status, len(p.thread), bool(p.proposal))
                        for p in (dati.points if dati else [])),
                  len(dati.free_thread) if dati else 0)
        if chiave == self._review_key and not force:
            return
        self._review_key = chiave
        pagina.set_data(dati)
        if pagina.free_mode:
            pagina.show_free(dati, forge.review_perplexities())
            return
        punto = dati.point(pagina.current_id) if dati and pagina.current_id else None
        if punto is not None:
            pagina.show_point(punto)
        else:
            pagina.chat.view.clear()
            pagina.chat.view.add_message("assistant", t(
                "La revisione è facoltativa. Premi «Prepara i punti da discutere» per rivedere "
                "con me, un punto alla volta, quello che la ricerca ha trovato e quello che non "
                "mi torna; oppure passa direttamente alla stesura.") if not (dati and dati.points)
                else t("Scegli un punto nell'elenco: te lo presento e ne parliamo. Puoi "
                       "saltarlo, o passare alla stesura quando vuoi."))
            pagina.chat.set_notebook("")

    def _review_open(self, point_id: str) -> None:
        forge = self._forge()
        dati = forge.review_data() if forge else None
        punto = dati.point(point_id) if dati else None
        if punto is None:
            return
        self.fg_review.show_point(punto)
        self._review_key = ()

    def _review_open_free(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        if forge.state.phase != "review":
            forge.go_to("review")
            self._forge_paint(forge)
        self.fg_review.free_mode = True
        self.fg_review.show_free(forge.review_data(), forge.review_perplexities())
        self.fg_center.setCurrentWidget(self.fg_review)
        self._review_key = ()

    def _review_generate(self) -> None:
        if self._chat_wait():
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return
        vista = self.fg_review.chat.view

        def avanzamento(nota) -> None:
            if isinstance(nota, str):
                vista.add_notice(nota)

        def fatto(_dati) -> None:
            self._review_refresh(forge, force=True)
            self._forge_paint(forge)

        def fallito(messaggio: str) -> None:
            self._review_refresh(forge, force=True)
            vista.add_notice(
                t("La preparazione dei punti non è riuscita: {0}\n\nQuello che era già stato "
                  "trovato è salvato.").format(messaggio),
                t("Riprova"), self._review_generate)

        self._forge_job(
            lambda p, n: forge.review_generate(progress=p, note=n,
                                               should_stop=lambda: self._stop_requested),
            fatto, "Preparo i punti da discutere…", progressable=True, stoppable=True,
            on_error=fallito, on_note=avanzamento,
            activity=t("Sto preparando i punti da discutere…"),
            steps=t("Sto preparando i punti da discutere ({0} di {1})…"), kind="research")

    def _review_send(self, testo: str) -> None:
        pagina = self.fg_review
        testo = testo.strip()
        allegati = pagina.chat.composer.take_attachments()
        if not testo and not allegati:
            return
        vista = pagina.chat.view
        if self._rchat_busy or self._chat_busy or self._qchat_busy:
            vista.add_notice(t("Aspetta che l'assistente finisca di rispondere."))
            return
        if self._workers:
            self._busy_notice()
            return
        if not pagina.free_mode and not pagina.current_id:
            vista.add_notice(t("Scegli prima un punto nell'elenco, oppure apri la chat libera."))
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return
        mostrato = testo
        if allegati:
            mostrato += ("\n\n" if mostrato else "") + t("Allegati: {0}").format(
                ", ".join(p.name for p in allegati))
        pagina.chat.composer.clear()
        vista.add_message("user", mostrato)
        vista.scroll_to_end()
        self._review_reply(forge, pagina.free_mode, pagina.current_id, testo or None, allegati)

    def _review_reply(self, forge, libera: bool, point_id: str, testo: str | None,
                      allegati: list) -> None:
        pagina = self.fg_review
        vista = pagina.chat.view
        slug = self.pm.active_slug()
        self._rchat_busy = True
        pagina.chat.composer.set_streaming(True)
        stato: dict = {"bolla": None}

        def bolla():
            if stato["bolla"] is None:
                stato["bolla"] = vista.begin_reply()
            return stato["bolla"]

        def lavoro(_avanzamento, nota):
            def arriva(tipo: str, pezzo: str) -> None:
                if self._stop_requested:
                    raise iv.Stopped()
                nota((tipo, pezzo))

            def cercato(riga: str) -> None:
                nota(("notice", riga))

            try:
                if libera:
                    r = forge.review_free_talk(testo, allegati, on_delta=arriva, on_search=cercato)
                else:
                    r = forge.review_talk(point_id, testo, allegati, on_delta=arriva,
                                          on_search=cercato)
                return "fatto", r
            except iv.Stopped:
                return "fermato", None

        def pezzo(nota) -> None:
            tipo, arrivato = nota
            if tipo == "notice":
                vista.add_notice(arrivato)
            elif tipo == "content":
                bolla().append(arrivato)
            elif tipo == "reasoning":
                bolla().reasoning()
            elif tipo == "restart" and stato["bolla"] is not None:
                stato["bolla"].reset()

        def fatto(esito) -> None:
            pagina.chat.composer.set_streaming(False)
            stato_, risultato = esito
            if stato_ == "fermato":
                bolla().finish(note=t("Risposta interrotta."))
                self._rchat_busy = False
                return
            risposta, _log, _note = risultato
            bolla().finish(risposta)
            self._review_after(slug, libera, point_id)

        def fallito(messaggio: str) -> None:
            pagina.chat.composer.set_streaming(False)
            bolla().finish(note=t("Nessuna risposta."))
            vista.add_notice(
                t("La risposta non è arrivata: {0}\n\nQuello che hai scritto è salvato.")
                .format(messaggio), t("Riprova"), self._review_retry)
            self._rchat_busy = False

        def interrotto() -> None:
            pagina.chat.composer.set_streaming(False)
            bolla().finish(note=t("Risposta interrotta."))
            self._rchat_busy = False

        self._review_retry_args = (libera, point_id)
        self._forge_job(lavoro, fatto, "L'assistente risponde…", progressable=True,
                        stoppable=True, on_error=fallito, on_note=pezzo,
                        activity=t("Sto preparando la risposta…"), kind="chat",
                        on_cancel=interrotto)

    def _review_retry(self) -> None:
        if self._rchat_busy:
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return
        libera, point_id = getattr(self, "_review_retry_args", (False, ""))
        self._review_reply(forge, libera, point_id, None, [])

    def _review_after(self, slug: str, libera: bool, point_id: str) -> None:
        """Dopo ogni risposta: cosa registrerebbe l'app. Non registra niente: propone."""
        forge = self._forge_for(slug, with_client=True)

        def fatto(proposta) -> None:
            self._rchat_busy = False
            if slug != self.pm.active_slug():
                return
            if proposta:
                self.fg_review.card.open(proposta, point_kind=self.fg_review.current_kind)
            dati = forge.review_data()
            self._review_key = ()
            self.fg_review.set_data(dati)

        def fallito(_messaggio: str) -> None:
            self._rchat_busy = False  # non è grave: il prossimo scambio riprende anche questo

        self._forge_job(
            (lambda: forge.review_free_propose()) if libera
            else (lambda: forge.review_propose(point_id)),
            fatto, "Registro le decisioni…", on_error=fallito,
            activity=t("Sto registrando le decisioni…"), kind="chat", quiet_error=True)

    def _review_confirm(self, kind: str, text: str, keep_note: bool) -> None:
        forge = self._forge()
        if forge is None:
            return
        pagina = self.fg_review
        v = pagina.card.values()
        vista = pagina.chat.view
        try:
            if pagina.free_mode:
                punto = forge.review_free_confirm(
                    text, kind, text, note=v["note"], keep_note=keep_note,
                    structure_note=v["structure_note"], by_author=v["by_author"])
            else:
                punto = forge.review_confirm(
                    pagina.current_id, kind, text, note=v["note"], keep_note=keep_note,
                    source=v["source"], url=v["url"], structure_note=v["structure_note"],
                    by_author=v["by_author"])
        except ValueError as exc:
            vista.add_notice(str(exc))
            return
        vista.add_notice(t("Registrato: {0}").format(punto.title))
        self._review_key = ()
        self._review_next(forge, punto.id)

    def _review_dismiss(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        forge.review_clear_proposal(None if self.fg_review.free_mode
                                    else self.fg_review.current_id)

    def _review_skip_point(self) -> None:
        forge = self._forge()
        pagina = self.fg_review
        if forge is None or not pagina.current_id:
            return
        punto = forge.review_skip_point(pagina.current_id)
        pagina.chat.view.add_notice(t("Rimandato: {0}").format(punto.title))
        self._review_key = ()
        self._review_next(forge, punto.id)

    def _review_next(self, forge, after: str) -> None:
        """Dopo un punto chiuso o saltato: il prossimo da decidere, o il commiato."""
        pagina = self.fg_review
        dati = forge.review_data()
        pagina.set_data(dati)
        prossimo = pagina.next_open_id(dati, after) if dati else ""
        if prossimo:
            pagina.select(prossimo)
            return
        pagina.chat.view.add_notice(
            t("Hai trattato tutti i punti. Puoi passare alla stesura, oppure restare a parlare: "
              "nella chat libera puoi portare altri dubbi o chiedermi di verificare qualcosa."),
            t("Passa alla stesura"), self._forge_advance)
        self._forge_paint(forge)

    def _review_skip_all(self) -> None:
        """«Salta la revisione»: i punti aperti restano da decidere, senza effetti; si va avanti."""
        if self._chat_wait():
            return
        forge = self._forge()
        if forge is None:
            return
        forge.review_skip_all()
        self._forge_advance()

    def _forge_decline_research(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        self._forge_say("assistant", forge.decline_research())
        self._forge_paint(forge)

    # ---- fase 4 -----------------------------------------------------------
    def _forge_style_contract(self, forge) -> str:
        style_id = self.fg_style.currentData()
        if style_id is None:
            return ""
        row = project_schema.get_style_row(self.pm.paths(self.pm.active_slug()).db, int(style_id))
        return build_style_contract(StyleProfile.model_validate(row["data"])) if row else ""

    def _forge_write_next(self) -> None:
        forge = self._forge(with_client=True)
        if forge is None:
            return
        contratto = self._forge_style_contract(forge)

        def fatto(risultato) -> None:
            if risultato is None:
                self._forge_say("assistant", t("Tutti i capitoli previsti sono già su disco."))
            elif risultato.stopped:
                self._forge_say("assistant", t(
                    "Fermato a metà capitolo: le sezioni scritte restano da parte e si "
                    "riprende da quella che manca. Il capitolo non è ancora su disco."))
            else:
                self._forge_say("assistant", t("Scritto su disco: {0}").format(risultato.line()))
            self._forge_paint(forge)

        def nota(n) -> None:
            if isinstance(n, tuple):
                self._forge_note_detail(n)

        self._forge_job(
            lambda p, n: forge.write_next(style_contract=contratto, note=n,
                                          should_stop=lambda: self._stop_requested),
            fatto, "Scrivo il capitolo…", progressable=True, stoppable=True, on_note=nota,
            activity=t("Sto scrivendo il prossimo capitolo…"))

    def _forge_write_all(self) -> None:
        forge = self._forge(with_client=True)
        if forge is None:
            return
        contratto = self._forge_style_contract(forge)
        righe: list[str] = []

        def avanzamento(nota) -> None:
            if isinstance(nota, tuple):
                self._forge_note_detail(nota)  # «sezione 3 di 6», nel riquadro di stato
                return
            righe.append(str(nota))
            self._forge_show("\n".join(f"- {r}" for r in righe))

        def fatto(report) -> None:
            self._forge_show(report.summary())
            self._forge_paint(forge)

        self._forge_job(
            lambda p, n: forge.write_all(style_contract=contratto, progress=p, note=n,
                                         should_stop=lambda: self._stop_requested),
            fatto, "Scrivo i capitoli…", progressable=True, stoppable=True, on_note=avanzamento,
            activity=t("Sto scrivendo i capitoli…"),
            steps=t("Sto scrivendo il capitolo {0} di {1}…"),
        )

    def _forge_redo_short(self) -> None:
        """«Riscrivi i capitoli troppo corti»: li mette da parte, non li cancella, e li riscrive."""
        if self._chat_wait():
            return
        forge = self._forge(with_client=True)
        if forge is None:
            return
        corti = forge.short_chapters()
        if not corti:
            QMessageBox.information(self, t("Niente da rifare"),
                                    t("Nessun capitolo è molto sotto il suo budget di parole."))
            return
        elenco = "\n".join(
            t("Capitolo {0}: {1} parole su {2}").format(n, parole, budget)
            for n, parole, budget in corti)
        scelta = QMessageBox.question(
            self, t("Riscrivere i capitoli troppo corti?"),
            t("Questi capitoli sono sotto il 60% del loro budget di parole:\n\n{0}\n\nLi metto da "
              "parte (restano in una cartella di riserva, non si cancella niente) e li "
              "riscrivo. Procedo?").format(elenco))
        if scelta != QMessageBox.StandardButton.Yes:
            return
        contratto = self._forge_style_contract(forge)

        def avanzamento(nota) -> None:
            if isinstance(nota, tuple):
                self._forge_note_detail(nota)

        def fatto(esito) -> None:
            messi, report = esito
            self._forge_show(t("Messi da parte: {0}").format(", ".join(p.name for p in messi))
                             + "\n\n" + report.summary())
            self._forge_paint(forge)

        self._forge_job(
            lambda p, n: forge.redo_short_chapters(style_contract=contratto, progress=p, note=n,
                                                   should_stop=lambda: self._stop_requested),
            fatto, "Riscrivo i capitoli troppo corti…", progressable=True, stoppable=True,
            on_note=avanzamento, activity=t("Sto riscrivendo i capitoli troppo corti…"),
            steps=t("Sto scrivendo il capitolo {0} di {1}…"),
        )

    def _forge_write_matter(self) -> None:
        forge = self._forge(with_client=True)
        if forge is None:
            return
        quale = self.fg_matter.currentData()
        contratto = self._forge_style_contract(forge)

        def fatto(path) -> None:
            self._forge_say("assistant", t("Scritto su disco: {0}").format(path.name))
            self._forge_paint(forge)

        self._forge_job(lambda: forge.write_matter(quale, style_contract=contratto), fatto,
                        "Scrivo la sezione…", activity=t("Sto scrivendo la sezione…"))

    # ---- fase 5 -----------------------------------------------------------
    def _forge_pick_cover(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        percorso, _ = QFileDialog.getOpenFileName(
            self, t("Immagine di copertina"), "",
            t("Immagini (*.jpg *.jpeg *.png *.tif *.tiff);;Tutti i file (*)"),
        )
        if not percorso:
            return
        # Senza book.yaml la copertina lo scrive: con la misura della tendina, non con quella
        # predefinita del progetto.
        forge.cfg.production.trim = self.fg_trim.currentData() or "a5"
        forge.set_cover(Path(percorso))
        self._forge_paint(forge)

    def _forge_produce(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        forge.cfg.production.trim = self.fg_trim.currentData() or "a5"
        path = forge.produce()
        self._forge_show(path.read_text(encoding="utf-8"))
        self._forge_paint(forge)

    # ---- fase 6 -----------------------------------------------------------
    def _forge_build(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        formati = [f for f, casella in (("pdf", self.fg_pdf), ("docx", self.fg_docx),
                                        ("epub", self.fg_epub), ("md", self.fg_md))
                   if casella.isChecked()]
        if not formati:
            QMessageBox.information(self, t("Nessun formato"), t("Scegli almeno un formato."))
            return
        senza_copertina = self.fg_no_cover.isChecked()

        def fatto(report) -> None:
            self._forge_show("```\n" + report.summary() + "\n```")
            if report.no_cover:
                # Non è un errore e non si ferma niente: ma l'autore deve saperlo, e il riquadro
                # è dove guarda.
                self._fg_warning = t("Composto senza copertina: non ne ho inventata una. Per "
                                     "averla scegli l'immagine di copertina nella fase di "
                                     "produzione e ricomponi.")
            self._forge_paint(forge)

        def fallito(messaggio: str) -> None:
            self._forge_show(messaggio)  # e il motivo sta anche nel riquadro di stato

        self._fg_warning = ""
        self._forge_job(
            lambda: forge.build(formats=formati, copy_without_cover=senza_copertina),
            fatto, "Compongo il libro…", on_error=fallito, activity=t("Sto componendo il libro…"),
        )

    # ---- fase 7 -----------------------------------------------------------
    def _forge_check(self) -> None:
        forge = self._forge()
        if forge is None:
            return

        def fatto(esito) -> None:
            testo, _ok = esito
            self._forge_show("```\n" + testo + "\n```\n\n" + t(
                "Questi controlli non sostituiscono Kindle Previewer per l'ebook né una copia "
                "stampata per la carta."))

        self._forge_job(forge.check, fatto, "Controllo i file prodotti…",
                        activity=t("Sto controllando i file prodotti…"))

    def _forge_handover(self) -> None:
        forge = self._forge()
        if forge is None:
            return
        path = forge.handover()
        self._forge_show(path.read_text(encoding="utf-8"))
        self._forge_paint(forge)

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
        self.up_format = self._format_combo(epub=False)
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
        # I segnaposto si leggono anche su un fondo scuro (colore calcolato dalla tavolozza).
        w.setStyleSheet(f"QLineEdit {{ placeholder-text-color: {muted_color(w)}; }}")
        lay = QVBoxLayout(w)
        lay.addWidget(self._hint(
            t("Scegli il progetto, la lingua in cui vuoi il libro e quali modelli di LM Studio usare. "
            "I modelli già caricati in memoria sono segnati in rosso con «(caricato)»: partono "
            "subito, gli altri vengono caricati al primo utilizzo e la prima operazione sarà più lenta.")
        ))

        head = QFormLayout()
        self.set_provider = QComboBox()
        self.set_provider.setToolTip(t("Chi esegue i modelli: il tuo computer o un servizio nel cloud."))
        self._fill_providers("lmstudio")
        self.set_provider.currentIndexChanged.connect(self._on_provider_changed)
        riga_fornitore = QHBoxLayout()
        riga_fornitore.addWidget(self.set_provider, 1)
        self.set_server_add = QPushButton(t("Aggiungi server…"))
        self.set_server_add.setToolTip(t("Aggiunge un server personalizzato (un router, un "
                                         "llama-server, un servizio compatibile con OpenAI)."))
        self.set_server_add.clicked.connect(self._server_add)
        self.set_server_edit = QPushButton(t("Modifica…"))
        self.set_server_edit.clicked.connect(self._server_edit)
        self.set_server_remove = QPushButton(t("Rimuovi"))
        self.set_server_remove.clicked.connect(self._server_remove)
        for pulsante in (self.set_server_add, self.set_server_edit, self.set_server_remove):
            pulsante.setMinimumHeight(44)
            riga_fornitore.addWidget(pulsante)
        head.addRow(t("Fornitore:"), riga_fornitore)
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
            t("Resta sul tuo computer, in un archivio condiviso con gli altri programmi e "
            "fuori dalle cartelle dei progetti. In alternativa puoi impostarla come variabile "
            "d'ambiente, che ha la precedenza.")
        )
        keyrow.addWidget(self.set_api_key, 1)
        lay.addLayout(keyrow)

        # Con un abbonamento non c'è nulla da incollare: si mostra se è collegato e, quando non
        # lo è, il comando che lo collega. Un campo chiave vuoto lascerebbe credere di doverne
        # trovare una.
        self.set_subscription = QLabel("")
        self.set_subscription.setWordWrap(True)
        self.set_subscription.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.set_subscription.setVisible(False)
        lay.addWidget(self.set_subscription)

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
        self.set_effort = QComboBox()
        for valore, etichetta in reasoning_choices():
            self.set_effort.addItem(etichetta, valore)
        self.set_effort.setToolTip(reasoning_tooltip())
        self.set_effort.setAccessibleName(t("Ragionamento"))
        form.addRow(t("Ragionamento (estrazione, conversazione, stesura):"), self.set_effort)
        self.set_effort_quick = QComboBox()
        self.set_effort_quick.addItem(t("Come l'altro livello"), "same")
        for valore, etichetta in reasoning_choices():
            self.set_effort_quick.addItem(etichetta, valore)
        self.set_effort_quick.setToolTip(t(
            "Quanto ragiona il modello nei compiti rapidi: giudizio di pertinenza delle pagine, "
            "interrogazioni di ricerca, riformulazioni, intento di ricerca nella chat. Un "
            "livello più basso li rende molto più veloci (un solo giudizio può costare "
            "migliaia di token di ragionamento). «Come l'altro livello» non cambia niente."))
        self.set_effort_quick.setAccessibleName(t("Ragionamento dei compiti rapidi"))
        form.addRow(t("Ragionamento (compiti rapidi):"), self.set_effort_quick)
        self.set_structured = QComboBox()
        for valore, etichetta in structured_choices():
            self.set_structured.addItem(etichetta, valore)
        self.set_structured.setToolTip(structured_tooltip())
        self.set_structured.setAccessibleName(t("Output strutturato"))
        form.addRow(t("Output strutturato:"), self.set_structured)
        # Campionamento: vuoto = decide il server (è il valore di serie). Se si scrive qualcosa,
        # vale per tutte le richieste.
        self.set_sampling: dict[str, QLineEdit] = {}
        riga = QHBoxLayout()
        for chiave, etichetta in self._SAMPLING_FIELDS:
            campo = QLineEdit()
            campo.setPlaceholderText(t("server"))
            campo.setMinimumHeight(32)
            campo.setAccessibleName(etichetta)
            campo.setToolTip(t("Vuoto: decide il server dei modelli. Se scrivi un valore, vale per "
                               "tutte le richieste."))
            self.set_sampling[chiave] = campo
            riga.addWidget(QLabel(etichetta + ":"))
            riga.addWidget(campo, 1)
        form.addRow(t("Campionamento:"), riga)
        lay.addLayout(form)
        for combo in (self.set_reasoning, self.set_embedding, self.set_heavy):
            combo.currentIndexChanged.connect(lambda _i, c=combo: self._mark_loaded_selection(c))

        self.set_as_default = QCheckBox(t("Usa questi modelli anche per i prossimi progetti"))
        self.set_as_default.setChecked(True)
        lay.addWidget(self.set_as_default)

        # --- ricerca sul web -------------------------------------------------
        # Spenta di serie: questo programma non è mai uscito in rete, e chi lavora su una
        # macchina staccata o su materiale riservato non deve scoprirlo dopo.
        lay.addWidget(self._hint(
            t("La fase di ricerca può cercare in rete i libri che esistono già e le fonti dei "
              "capitoli. È l'unica parte del programma che esce su internet, e resta spenta "
              "finché non la accendi.")
        ))
        ricerca = QHBoxLayout()
        self.set_research = QCheckBox(t("Cerca anche sul web"))
        self.set_research.setToolTip(t("Senza, la ricerca lavora solo sui testi che hai "
                                       "importato nel progetto."))
        ricerca.addWidget(self.set_research)
        ricerca.addWidget(QLabel(t("Motore:")))
        self.set_engine = QComboBox()
        for chiave, etichetta in SEARCH_ENGINES:
            self.set_engine.addItem(t(etichetta), chiave)
        ricerca.addWidget(self.set_engine, 1)
        self.set_research_key = QLineEdit()
        self.set_research_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.set_research_key.setPlaceholderText(t("chiave del motore, se ne vuole una"))
        ricerca.addWidget(self.set_research_key, 1)
        lay.addLayout(ricerca)

        # Le soglie e i domini della ricerca: visibili e modificabili qui, non nascosti nel codice.
        soglie = QHBoxLayout()
        soglie.addWidget(QLabel(t("Testo minimo di una pagina (caratteri):")))
        self.set_min_chars = QSpinBox()
        self.set_min_chars.setRange(0, 20000)
        self.set_min_chars.setSingleStep(50)
        self.set_min_chars.setMinimumHeight(44)
        self.set_min_chars.setToolTip(t("Dopo la pulizia, una pagina con meno prosa di così è «non "
                                        "leggibile» e non va mai al modello."))
        self.set_min_chars.setAccessibleName(t("Testo minimo di una pagina"))
        soglie.addWidget(self.set_min_chars)
        soglie.addWidget(QLabel(t("Pertinenza minima (0-1):")))
        self.set_min_rel = QDoubleSpinBox()
        self.set_min_rel.setRange(0.0, 1.0)
        self.set_min_rel.setSingleStep(0.05)
        self.set_min_rel.setDecimals(2)
        self.set_min_rel.setMinimumHeight(44)
        self.set_min_rel.setToolTip(t("Il modello giudica se ogni pagina serve al punto (0 = parla "
                                      "d'altro, 1 = molto utile). Sotto questa soglia la pagina è "
                                      "«non pertinente». 0 = nessun filtro."))
        self.set_min_rel.setAccessibleName(t("Pertinenza minima"))
        soglie.addWidget(self.set_min_rel)
        soglie.addStretch(1)
        lay.addLayout(soglie)

        # Giudizi rapidi con Jev (TypeSafe): spento finché non lo si accende qui.
        jev_riga = QHBoxLayout()
        self.set_jev = QCheckBox(t("Giudizi rapidi con Jev"))
        self.set_jev.setMinimumHeight(44)
        self.set_jev.setToolTip(t(
            "Usa Jev (typesafe.ai) per giudicare se una pagina serve al punto e per scegliere le "
            "sezioni migliori nelle pagine lunghe e nei PDF. Fuori dal computer vanno solo "
            "estratti (mai pagine o PDF interi) e il punto del capitolo. Se Jev non risponde si "
            "torna al modello locale."))
        jev_riga.addWidget(self.set_jev)
        self.set_jev_key = QLineEdit()
        self.set_jev_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.set_jev_key.setMinimumHeight(44)
        self.set_jev_key.setPlaceholderText(t("chiave di Jev (o la variabile TYPESAFE_API_KEY)"))
        self.set_jev_key.setAccessibleName(t("Chiave di Jev"))
        jev_riga.addWidget(self.set_jev_key, 1)
        lay.addLayout(jev_riga)
        jev_soglie = QHBoxLayout()
        jev_soglie.addWidget(QLabel(t("Punteggio minimo di Jev (0-3):")))
        self.set_jev_min = QDoubleSpinBox()
        self.set_jev_min.setRange(0.0, 3.0)
        self.set_jev_min.setSingleStep(0.1)
        self.set_jev_min.setDecimals(2)
        self.set_jev_min.setMinimumHeight(44)
        self.set_jev_min.setToolTip(t(
            "Scala di Jev: 0 fuori tema, 1 appena legata, 2 utile, 3 molto utile. Sotto questo "
            "punteggio una pagina è «non pertinente»."))
        self.set_jev_min.setAccessibleName(t("Punteggio minimo di Jev"))
        jev_soglie.addWidget(self.set_jev_min)
        jev_soglie.addWidget(QLabel(t("Estratto massimo mandato a Jev (caratteri):")))
        self.set_jev_chars = QSpinBox()
        self.set_jev_chars.setRange(200, 50000)
        self.set_jev_chars.setSingleStep(500)
        self.set_jev_chars.setMinimumHeight(44)
        self.set_jev_chars.setToolTip(t(
            "A Jev non va mai più testo di così per richiesta: più è lungo, più token consuma."))
        self.set_jev_chars.setAccessibleName(t("Estratto massimo mandato a Jev"))
        jev_soglie.addWidget(self.set_jev_chars)
        jev_soglie.addStretch(1)
        lay.addLayout(jev_soglie)
        esclusi = QHBoxLayout()
        esclusi.addWidget(QLabel(t("Domini esclusi:")))
        self.set_excluded = QLineEdit()
        self.set_excluded.setMinimumHeight(44)
        self.set_excluded.setPlaceholderText(t("separati da virgola; amazon.* vale per ogni paese"))
        self.set_excluded.setToolTip(t("Da questi domini non si scarica niente: il risultato si "
                                       "registra come «escluso»."))
        self.set_excluded.setAccessibleName(t("Domini esclusi"))
        esclusi.addWidget(self.set_excluded, 1)
        lay.addLayout(esclusi)

        fetcher_lay = QHBoxLayout()
        fetcher_lay.addWidget(QLabel(t("Servizio esterno per le pagine bloccate:")))
        self.set_external_fetcher = QLineEdit()
        self.set_external_fetcher.setMinimumHeight(44)
        self.set_external_fetcher.setPlaceholderText(t("http://127.0.0.1:8191/v1 (vuoto = spento)"))
        self.set_external_fetcher.setToolTip(t("Indirizzo del servizio compatibile FlareSolverr (/v1) per risolvere CAPTCHA e blocchi."))
        self.set_external_fetcher.setAccessibleName(t("Servizio esterno per le pagine bloccate"))
        fetcher_lay.addWidget(self.set_external_fetcher, 1)
        fetcher_lay.addWidget(QLabel(t("Tempo massimo per pagina (s):")))
        self.set_external_timeout = QSpinBox()
        self.set_external_timeout.setRange(5, 600)
        self.set_external_timeout.setValue(60)
        self.set_external_timeout.setMinimumHeight(44)
        self.set_external_timeout.setToolTip(t(
            "Quanto aspetta il servizio esterno prima di arrendersi su una pagina "
            "(maxTimeout nella richiesta). Predefinito: 60 secondi."))
        self.set_external_timeout.setAccessibleName(t("Tempo massimo per pagina"))
        fetcher_lay.addWidget(self.set_external_timeout)
        lay.addLayout(fetcher_lay)

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
        self._fill_providers(f"custom:{cfg.llm.provider_id}" if cfg.llm.provider_id
                             else cfg.llm.provider)
        self.set_effort.setCurrentIndex(max(self.set_effort.findData(cfg.llm.reasoning_effort), 0))
        self.set_effort_quick.setCurrentIndex(max(self.set_effort_quick.findData(
            cfg.llm.reasoning_effort_quick), 0))
        self.set_structured.setCurrentIndex(max(self.set_structured.findData(
            cfg.llm.structured_mode()), 0))
        for chiave, campo in self.set_sampling.items():
            valore = getattr(cfg.llm, chiave)
            campo.setText("" if valore is None else f"{valore:g}")
        self.set_base_url.setText(cfg.llm.base_url)
        self.set_api_key.setText(self._chiave_in_uso(cfg))
        self.set_research.setChecked(cfg.research.enabled)
        self.set_engine.setCurrentIndex(max(self.set_engine.findData(cfg.research.engine), 0))
        self.set_research_key.setText(cfg.research.api_key)
        self.set_min_chars.setValue(cfg.research.min_page_chars)
        self.set_min_rel.setValue(cfg.research.min_relevance)
        self.set_jev.setChecked(cfg.research.jev_enabled)
        self.set_jev_key.setText(cfg.research.jev_api_key)
        self.set_jev_min.setValue(cfg.research.jev_min_score)
        self.set_jev_chars.setValue(cfg.research.jev_max_chars)
        self.set_excluded.setText(", ".join(cfg.research.excluded_domains))
        self.set_external_fetcher.setText(cfg.research.external_fetcher_url)
        self.set_external_timeout.setValue(int(cfg.research.external_fetcher_timeout_s))
        self._apply_provider_ui()
        self._load_models()

    # ---- fornitori e server personalizzati ---------------------------------------
    def _fill_providers(self, selected: str) -> None:
        """Riempie la tendina dei fornitori: quelli predefiniti, poi i server personalizzati."""
        self.set_provider.blockSignals(True)
        self.set_provider.clear()
        for key, info in PROVIDERS.items():
            self.set_provider.addItem(info["label"], key)
        try:
            personalizzati = llm_servers.list_custom()
        except Exception:
            personalizzati = []  # un registro illeggibile non impedisce di aprire le impostazioni
        for voce in personalizzati:
            self.set_provider.addItem(t("{0} (personalizzato)").format(voce.get("name", "?")),
                                      f"custom:{voce.get('id')}")
        self.set_provider.setCurrentIndex(max(self.set_provider.findData(selected), 0))
        self.set_provider.blockSignals(False)

    def _provider_name(self) -> str:
        """Il nome del fornitore scelto, senza l'indicazione di dove gira: «LM Studio», «Il mio router»."""
        dato = self.set_provider.currentData() or "lmstudio"
        if dato.startswith("custom:"):
            return str((llm_servers.get_custom(dato[len("custom:"):]) or {}).get("name") or dato)
        return str(PROVIDERS.get(dato, {}).get("label") or dato).split(" — ")[0]

    def _provider_parts(self) -> tuple[str, str]:
        """``(tipo, id)`` del fornitore scelto: per un server personalizzato il suo tipo di API e
        il suo id nel registro; per gli altri la chiave predefinita e l'id vuoto."""
        dato = self.set_provider.currentData() or "lmstudio"
        if dato.startswith("custom:"):
            pid = dato[len("custom:"):]
            voce = llm_servers.get_custom(pid)
            return (str((voce or {}).get("type") or "openai"), pid)
        return dato, ""

    def _server_dialog(self, voce: dict | None) -> dict | None:
        """La finestra del server: i dati inseriti, o ``None`` se si annulla."""
        dialogo = ServerDialog(self, voce)
        if dialogo.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialogo.values()

    def _server_add(self) -> None:
        dati = self._server_dialog(None)
        if dati is None:
            return
        try:
            voce = llm_servers.add_custom(dati["name"], dati["base_url"], dati["tipo"],
                                          dati["api_key"], dati["reasoning_level"],
                                          dati.get("structured_output", "json_schema"))
        except Exception as exc:
            QMessageBox.warning(self, t("Non ho potuto aggiungere il server"), str(exc))
            return
        self._fill_providers(f"custom:{voce['id']}")
        self._on_provider_changed(self.set_provider.currentIndex())

    def _server_edit(self) -> None:
        _tipo, pid = self._provider_parts()
        voce = llm_servers.get_custom(pid) if pid else None
        if voce is None:
            return
        dati = self._server_dialog(voce)
        if dati is None:
            return
        try:
            llm_servers.update_custom(pid, name=dati["name"], base_url=dati["base_url"],
                                      api_key=dati["api_key"],
                                      reasoning_level=dati["reasoning_level"],
                                      structured_output=dati.get("structured_output"))
        except Exception as exc:
            QMessageBox.warning(self, t("Non ho potuto modificare il server"), str(exc))
            return
        self._fill_providers(f"custom:{pid}")
        self._on_provider_changed(self.set_provider.currentIndex())

    def _server_remove(self) -> None:
        _tipo, pid = self._provider_parts()
        voce = llm_servers.get_custom(pid) if pid else None
        if voce is None:
            return
        scelta = QMessageBox.question(
            self, t("Rimuovere il server?"),
            t("Tolgo «{0}» dal registro dei fornitori. I progetti che lo usano dovranno "
              "scegliere un altro fornitore.").format(voce.get("name", pid)))
        if scelta != QMessageBox.StandardButton.Yes:
            return
        try:
            llm_servers.remove_custom(pid)
        except Exception as exc:
            QMessageBox.warning(self, t("Non ho potuto rimuovere il server"), str(exc))
            return
        self._fill_providers("lmstudio")
        self._on_provider_changed(self.set_provider.currentIndex())

    @staticmethod
    def _registra_chiave(provider: str, chiave: str) -> bool:
        """Salva la chiave nel registro comune. Vero se ci è riuscita.

        Se il registro non è scrivibile si torna a scriverla nel file del progetto: meglio una
        chiave in un posto meno buono che un fornitore che smette di funzionare senza spiegare
        perché.
        """
        if not chiave:
            return False
        try:
            Registry().set_api_key_for(provider, chiave)
            return True
        except Exception:
            return False

    @staticmethod
    def _chiave_in_uso(cfg) -> str:
        """La chiave da mostrare: quella del progetto se c'è, altrimenti quella del registro.

        Le chiavi nuove vanno nel registro comune, condiviso con gli altri programmi; quelle
        vecchie sono rimaste nel file del progetto e continuano a valere. Il campo mostra
        quella che verrà davvero usata, qualunque delle due sia.
        """
        if cfg.llm.api_key.strip():
            return cfg.llm.api_key
        try:
            if cfg.llm.provider_id:
                return cfg.llm.resolved_api_key()
            return Registry().api_key_for(cfg.llm.provider)
        except Exception:
            return ""  # un registro illeggibile lascia il campo vuoto, non impedisce di aprire

    def _apply_provider_ui(self) -> None:
        """Mostra il campo della chiave, o lo stato dell'abbonamento, secondo il fornitore."""
        nome = self.set_provider.currentData() or "lmstudio"
        _tipo, pid = self._provider_parts()
        info = PROVIDERS.get(nome, {})
        # Un server personalizzato ha la chiave facoltativa: il campo c'è sempre.
        needs_key = bool(pid) or bool(info.get("needs_key"))
        for widget in (self.set_key_label, self.set_api_key):
            widget.setVisible(needs_key)
        for pulsante in (self.set_server_edit, self.set_server_remove):
            pulsante.setEnabled(bool(pid))
        # Per un server personalizzato l'impostazione sta nella sua finestra, non qui.
        self.set_structured.setEnabled(not pid)
        self.set_structured.setToolTip(
            t("Per un server personalizzato si cambia nella sua finestra («Modifica…»).") if pid
            else structured_tooltip())
        testo = "" if pid else self._stato_abbonamento(nome)
        self.set_subscription.setText(testo)
        self.set_subscription.setVisible(bool(testo))

    @staticmethod
    def _stato_abbonamento(provider: str) -> str:
        """Riga che dice se l'abbonamento è utilizzabile, e cosa fare se non lo è.

        Vuota per i fornitori che non sono ad abbonamento. Non mostra mai un token: solo lo
        stato e, quando serve, il comando da lanciare.
        """
        try:
            from llmswitch import ABBONAMENTI, stato
        except ImportError:
            return ""
        if provider not in ABBONAMENTI:
            return ""
        try:
            s = stato(provider)
        except Exception:
            return ""
        if s["utilizzabile"]:
            quando = s["scade_fra_ore"]
            resta = t(" · si rinnova da sé") if s["rinnovabile"] else ""
            return t("Collegato: si usa il tuo abbonamento.") + (
                t(" Credenziale valida ancora {0} ore.").format(quando) if quando else ""
            ) + resta
        if s["rinnovabile"]:
            return t("Collegato, credenziale scaduta: verrà rinnovata da sola alla prossima richiesta.")
        return t("Non collegato. Esegui «{0}» una volta in un terminale: da lì in poi la "
                 "credenziale si rinnova da sé.").format(s["come_accedere"])

    def _on_provider_changed(self, _index: int) -> None:
        """Cambiando fornitore cambiano indirizzo e catalogo dei modelli."""
        _tipo, pid = self._provider_parts()
        if pid:
            # Un server personalizzato porta con sé indirizzo, chiave e ragionamento.
            voce = llm_servers.get_custom(pid) or {}
            self.set_base_url.setText(str(voce.get("base_url", "")))
            self.set_api_key.setText(str(voce.get("api_key", "")))
            livello = str(voce.get("reasoning_level", "inherit"))
            livello = "" if livello == "inherit" else livello
            self.set_effort.setCurrentIndex(max(self.set_effort.findData(livello), 0))
            self.set_structured.setCurrentIndex(max(self.set_structured.findData(
                str(voce.get("structured_output", "json_schema"))), 0))
        else:
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
        tipo, pid = self._provider_parts()
        llm_cfg = cfg.llm.model_copy(update={
            "provider": tipo,
            "provider_id": pid,
            "base_url": base,
            "api_key": self.set_api_key.text().strip(),
        })

        info = {} if pid else PROVIDERS.get(llm_cfg.provider, {})
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
            info = {} if pid else PROVIDERS.get(llm_cfg.provider, {})
            hint = (
                t("Controlla la chiave di accesso") if info.get("needs_key")
                else (t("Avvia il server dei modelli in LM Studio") if llm_cfg.provider == "lmstudio"
                      and not pid else t("Controlla che il server sia acceso e l'indirizzo giusto"))
            )
            self.set_status.setText(t("{0} non risponde su {1}. {2}. ({3})").format(self._provider_name(), base, hint, msg))

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
            self.set_status.setText(t("Nessun modello rilevato su «{0}».").format(self._provider_name()))
            return
        loaded = [m.id for m in models if m.loaded]
        text = t("{0} modelli su «{1}» · caricati ora: ").format(len(models), self._provider_name()) + (", ".join(loaded) if loaded else t("nessuno"))
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
            label = t("{0}  (caricato)").format(m.id) if m.loaded else m.id
            # Quanto costa, se si paga a consumo; quanto pesa, se gira sul tuo computer: sono
            # le due cose che fanno scegliere, e nessun modello ha entrambe.
            for dettaglio in (m.price_label(), m.size_label()):
                if dettaglio:
                    label += t("  ·  {0}").format(dettaglio)
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

    #: I parametri di campionamento della tab Impostazioni: (chiave di ``LLMConfig``, etichetta).
    _SAMPLING_FIELDS = (("temperature", "temperature"), ("top_p", "top_p"), ("top_k", "top_k"),
                        ("min_p", "min_p"), ("presence_penalty", "presence_penalty"),
                        ("repetition_penalty", "repetition_penalty"))

    def _read_sampling(self) -> dict | None:
        """I valori scritti nei campi del campionamento (vuoto = ``None``), o ``None`` se uno è
        sbagliato (e lo si dice)."""
        letti: dict = {}
        for chiave, campo in self.set_sampling.items():
            testo = campo.text().strip().replace(",", ".")
            if not testo:
                letti[chiave] = None
                continue
            try:
                letti[chiave] = int(testo) if chiave == "top_k" else float(testo)
                LLMConfig.model_validate({chiave: letti[chiave]})
            except ValueError:  # (la ValidationError di pydantic è un ValueError)
                QMessageBox.warning(
                    self, t("Valore non valido"),
                    t("«{0}» non è un valore accettabile per {1}. Lascia il campo vuoto per far "
                      "decidere al server.").format(campo.text(), chiave))
                return None
        return letti

    def _save_settings(self) -> None:
        campionamento = self._read_sampling()
        if campionamento is None:
            return
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
        provider, provider_id = self._provider_parts()
        # La chiave va nel registro comune, non nel file del progetto: è la stessa credenziale
        # per tutti i progetti e per gli altri programmi, e i file di progetto finiscono nei
        # commit. Quella eventualmente già presente nel file non si cancella — vale ancora, e
        # cancellarla toglierebbe l'accesso a chi non ha ancora popolato il registro.
        if provider_id:
            # Un server personalizzato ha la sua chiave, nella sua voce del registro.
            try:
                voce = llm_servers.get_custom(provider_id) or {}
                llm_servers.update_custom(
                    provider_id, name=str(voce.get("name", "")), base_url=base,
                    api_key=self.set_api_key.text().strip(),
                    reasoning_level=str(voce.get("reasoning_level", "inherit")),
                    structured_output=str(voce.get("structured_output", "json_schema")))
                chiave_registro = True
            except Exception:
                chiave_registro = False
        else:
            chiave_registro = self._registra_chiave(provider, self.set_api_key.text().strip())
        new = cfg.model_copy(update={
            "embedding": profile or cfg.embedding,
            "llm": cfg.llm.model_copy(update={
                "provider": provider,
                "provider_id": provider_id,
                "base_url": base,
                "api_key": "" if chiave_registro else self.set_api_key.text().strip(),
                "models": roles,
                # Il ragionamento lo sceglie l'utente e basta: la tendina mostra e salva la sua
                # scelta, senza correggerla. Vuoto = «lascia decidere al server».
                "reasoning_effort": self.set_effort.currentData() or "",
                "reasoning_effort_quick": ("same" if self.set_effort_quick.currentData() is None
                                           else self.set_effort_quick.currentData()),
                "structured_output": self.set_structured.currentData() or "json_schema",
                **campionamento,
            }),
            "project": ProjectSettings(language=language),
            "research": cfg.research.model_copy(update={
                "enabled": self.set_research.isChecked(),
                "engine": self.set_engine.currentData() or "duckduckgo",
                "api_key": self.set_research_key.text().strip(),
                "min_page_chars": self.set_min_chars.value(),
                "min_relevance": self.set_min_rel.value(),
                "jev_enabled": self.set_jev.isChecked(),
                "jev_api_key": self.set_jev_key.text().strip(),
                "jev_min_score": self.set_jev_min.value(),
                "jev_max_chars": self.set_jev_chars.value(),
                "excluded_domains": [d.strip() for d in
                                     self.set_excluded.text().replace(";", ",").split(",")
                                     if d.strip()],
                "external_fetcher_url": self.set_external_fetcher.text().strip(),
                "external_fetcher_timeout_s": float(self.set_external_timeout.value()),
            }),
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
