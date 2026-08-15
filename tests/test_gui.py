"""Smoke test della GUI: costruzione della finestra in modalità offscreen (nessun display)."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from pathlib import Path

from PySide6.QtWidgets import QApplication

from bookwriter.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_mainwindow_builds(qapp, temp_workspace: Path) -> None:
    win = MainWindow()
    assert win.tabs.count() == 15
    titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    # Titoli funzionali (orientati al compito dell'utente), non ai nomi interni del motore.
    for expected in (
        "Importa testi",
        "Prepara contenuti",
        "Componi il libro",
        "Struttura (modifica)",
        "Stile di scrittura",
        "Completa i contenuti",
        "Riepilogo progetto",
        "Riscrivi in altro stile",
        "Traduci",
        "Coda di lavorazione",
        "Leggi il libro",
        "Scrivi da zero",
        "Aggiorna un libro",
        "Consumi dei modelli",
        "Impostazioni",
    ):
        assert any(expected in t for t in titles), expected
    win.close()


def test_settings_lists_models_and_marks_loaded(qapp, temp_workspace: Path) -> None:
    """Gli elenchi si popolano dai modelli di LM Studio; quelli caricati sono marcati e rossi."""
    from PySide6.QtCore import Qt

    from bookwriter.llm import ModelInfo

    win = MainWindow()
    win.pm.create("Progetto modelli")
    win._models = [
        ModelInfo(id="qwen3-8b-instruct", type="llm", state="loaded"),
        ModelInfo(id="qwen3-8b", type="llm", state="not-loaded"),
        ModelInfo(id="text-embedding-nomic-embed-text-v1.5", type="embeddings", state="loaded"),
    ]
    win._fill_model_combos()

    chat_items = [win.set_reasoning.itemText(i) for i in range(win.set_reasoning.count())]
    assert chat_items == ["qwen3-8b-instruct  (caricato)", "qwen3-8b"]
    assert win.set_reasoning.currentData() == "qwen3-8b-instruct"
    assert win.set_reasoning.itemData(0, Qt.ItemDataRole.ForegroundRole) == win._LOADED_COLOR
    assert win.set_reasoning.itemData(1, Qt.ItemDataRole.ForegroundRole) is None
    # Il modello scelto è caricato: la casella chiusa è rossa.
    assert win._LOADED_COLOR.name() in win.set_reasoning.styleSheet()

    # L'elenco embedding mostra solo i modelli di quel tipo; 'heavy' ammette "nessuno".
    assert [win.set_embedding.itemText(i) for i in range(win.set_embedding.count())] == [
        "text-embedding-nomic-embed-text-v1.5  (caricato)"
    ]
    assert win.set_heavy.currentData() == ""
    assert "caricati ora" in win.set_status.text()
    win.close()


def test_settings_saves_chosen_models(qapp, temp_workspace: Path) -> None:
    """Salvando, la scelta finisce nella config di progetto e nei default globali."""
    from bookwriter.config import load_global_config
    from bookwriter.llm import ModelInfo

    win = MainWindow()
    win.pm.create("Progetto salva")
    win._models = [
        ModelInfo(id="qwen3-8b", type="llm", state="not-loaded"),
        ModelInfo(id="text-embedding-nomic-embed-text-v1.5", type="embeddings", state="loaded"),
    ]
    # Nessuna chiamata di rete: popoliamo gli elenchi a mano.
    win.set_base_url.setText("http://localhost:1234/v1")
    win._fill_model_combos()
    win.set_reasoning.setCurrentIndex(win.set_reasoning.findData("qwen3-8b"))
    win._save_settings()

    slug = win.pm.active_slug()
    assert win.pm.config_for(slug).llm.models.reasoning == "qwen3-8b"
    assert load_global_config().llm.models.reasoning == "qwen3-8b"
    win.close()


def test_project_bar_reflects_projects(qapp, temp_workspace: Path) -> None:
    win = MainWindow()
    win.pm.create("Progetto GUI")
    win._reload_projects()
    assert win.project_combo.count() >= 1
    # Nessun crash nel calcolo delle info di progetto.
    win._refresh_project_info()
    win.close()


def test_settings_saves_language_and_switches_project(qapp, temp_workspace: Path) -> None:
    """La lingua scelta finisce nella config del progetto; la tendina progetti cambia l'attivo."""
    from bookwriter.llm import ModelInfo

    win = MainWindow()
    first = win.pm.create("Primo")
    second = win.pm.create("Secondo")
    win._models = [
        ModelInfo(id="qwen3-8b", type="llm", state="loaded"),
        ModelInfo(id="text-embedding-nomic-embed-text-v1.5", type="embeddings", state="loaded"),
    ]
    win._sync_settings_project()
    assert win.set_project.currentData() == second.slug  # l'ultimo creato è l'attivo

    # Torna al primo progetto dalla tendina del pannello: cambia l'attivo e la barra in alto.
    win.set_project.setCurrentIndex(win.set_project.findData(first.slug))
    assert win.pm.active_slug() == first.slug
    assert win.project_combo.currentData() == first.slug

    win._fill_model_combos()
    win.set_language.setCurrentIndex(win.set_language.findData("hu"))
    win._save_settings()
    assert win.pm.config_for(first.slug).project.language == "hu"
    # L'altro progetto non è stato toccato.
    assert win.pm.config_for(second.slug).project.language == ""
    win.close()


def test_ideas_list_shows_and_filters_nodes(qapp, temp_workspace: Path) -> None:
    """Le idee estratte si vedono nell'elenco e il filtro le restringe."""
    from sqlmodel import Session

    from bookwriter.db.project_schema import Document, Node, make_engine

    win = MainWindow()
    project = win.pm.create("Progetto idee")
    ppaths = win.pm.paths(project.slug)
    with Session(make_engine(ppaths.db)) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="Appunti")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        ses.add_all([
            Node(document_id=doc.id, raw_text="Il cielo è azzurro.", summary="colore del cielo",
                 type="fatto", themes=["meteo"], entities=[], salience=0.8),
            Node(document_id=doc.id, raw_text="Domani pioverà.", summary="previsione",
                 type="fatto", themes=["meteo", "futuro"], entities=[], salience=0.6),
        ])
        ses.commit()

    win._refresh_ideas()
    assert win.ideas_tree.topLevelItemCount() == 2
    assert "2 idee in tutto" in win.ideas_count.text()

    # Il dettaglio mostra il testo originale, non la sintesi soltanto.
    win.ideas_tree.setCurrentItem(win.ideas_tree.topLevelItem(0))
    assert "pioverà" in win.idea_detail.toPlainText()

    win.ideas_filter.setText("azzurro")
    hidden = [win.ideas_tree.topLevelItem(i).isHidden() for i in range(2)]
    assert hidden.count(False) == 1
    assert "1 corrispondono al filtro" in win.ideas_count.text()
    win.close()


def test_usage_table_records_tokens_per_activity(qapp, temp_workspace: Path) -> None:
    """I token consumati finiscono nella tabella, divisi per progetto e attività."""
    win = MainWindow()
    project = win.pm.create("Progetto consumi")
    client = win._client(project.slug)  # registrato fra i client dell'operazione
    win._begin_usage("Estraggo le idee…")
    client._track({"prompt_tokens": 1200, "completion_tokens": 8000, "total_tokens": 9200}, "qwen3-8b")
    win._tick_usage()  # rilevazione intermedia, come ogni 10 secondi
    client._track({"prompt_tokens": 300, "completion_tokens": 900, "total_tokens": 1200}, "qwen3-8b")
    win._end_usage()

    win._refresh_usage()
    top = win.usage_tree.topLevelItem(0)
    assert top.text(0) == "Progetto consumi"
    assert top.child(0).text(0) == "Estraggo le idee"
    # Le rilevazioni intermedie aggiornano la stessa riga invece di sommarsi.
    assert top.child(0).text(3) == "1.500"  # token inviati
    assert top.child(0).text(4) == "8.900"  # token generati
    assert top.child(0).text(2) == "2"      # richieste al modello
    assert "10.400 token" in win.usage_status.text()
    win.close()


def test_provider_switch_shows_key_field_and_prices(qapp, temp_workspace: Path) -> None:
    """Scegliendo un fornitore cloud compare la chiave; i modelli mostrano il prezzo."""
    from bookwriter.llm import ModelInfo

    win = MainWindow()
    win.pm.create("Progetto fornitore")
    assert win.set_api_key.isHidden() is True  # LM Studio non richiede chiave

    win.set_provider.setCurrentIndex(win.set_provider.findData("nebius"))
    assert win.set_base_url.text() == "https://api.tokenfactory.nebius.com/v1"
    assert win.set_api_key.isHidden() is False

    win._models = [
        ModelInfo(id="Qwen/Qwen3-235B-A22B-Instruct-2507", type="?", state="?", price_in=0.20, price_out=0.60),
        ModelInfo(id="Qwen/Qwen3-Embedding-8B", type="embeddings", state="?", price_in=0.01, price_out=0.0),
    ]
    win._fill_model_combos()
    chat_idx = win.set_reasoning.findData("Qwen/Qwen3-235B-A22B-Instruct-2507")
    assert "in $0.20 / out $0.60 per 1M token" in win.set_reasoning.itemText(chat_idx)
    embed_idx = win.set_embedding.findData("Qwen/Qwen3-Embedding-8B")
    assert embed_idx >= 0 and "in $0.01 per 1M token" in win.set_embedding.itemText(embed_idx)
    win.set_embedding.setCurrentIndex(embed_idx)
    # Il modello configurato prima, assente da questo catalogo, resta visibile ma segnalato.
    assert "non disponibile presso questo fornitore" in win.set_reasoning.itemText(0)
    win.set_reasoning.setCurrentIndex(chat_idx)

    # La chiave viene salvata insieme al fornitore.
    win.set_api_key.setText("chiave-di-prova")
    win._save_settings()
    cfg = win.pm.config_for(win.pm.active_slug())
    assert cfg.llm.provider == "nebius"
    assert cfg.llm.api_key == "chiave-di-prova"
    assert cfg.llm.models.reasoning == "Qwen/Qwen3-235B-A22B-Instruct-2507"
    win.close()


def test_usage_shows_cost_for_paid_models(qapp, temp_workspace: Path) -> None:
    """Con un fornitore a pagamento il consumo diventa anche una spesa in dollari."""
    from bookwriter.llm import ModelInfo

    win = MainWindow()
    project = win.pm.create("Progetto costi")
    win._models = [ModelInfo(id="qwen-cloud", type="?", state="?", price_in=0.20, price_out=0.60)]
    client = win._client(project.slug)
    win._begin_usage("Estraggo le idee…")
    client._track({"prompt_tokens": 700_000, "completion_tokens": 4_900_000, "total_tokens": 5_600_000}, "qwen-cloud")
    win._end_usage()

    win._refresh_usage()
    activity = win.usage_tree.topLevelItem(0).child(0)
    assert activity.text(6) == "$3,08"  # 0,7M×0,20 + 4,9M×0,60
    assert "spesa $3,08" in win.usage_status.text()
    win.close()


def test_ui_language_switch_translates_and_persists(qapp, temp_workspace: Path) -> None:
    """La tendina in alto a destra cambia lingua all'interfaccia e la scelta resta salvata."""
    from bookwriter.config import load_global_config
    from bookwriter.gui import i18n

    win = MainWindow()
    win.pm.create("Progetto lingua")
    assert i18n.current_language() == "it"
    assert win.tabs.tabText(0) == "1 · Importa testi"

    win.ui_lang_combo.setCurrentIndex(win.ui_lang_combo.findData("en"))
    assert i18n.current_language() == "en"
    assert win.tabs.tabText(0) == "1 · Import texts"
    assert win.tabs.tabText(14) == "Settings"
    assert load_global_config().ui.language == "en"  # ricordata per la prossima sessione

    win.ui_lang_combo.setCurrentIndex(win.ui_lang_combo.findData("hu"))
    assert win.tabs.tabText(0) == "1 · Szövegek importálása"
    assert win.tabs.tabText(1) == "2 · Tartalom előkészítése"

    win.ui_lang_combo.setCurrentIndex(win.ui_lang_combo.findData("ja"))
    assert win.tabs.tabText(0) == "1・テキストを取り込む"
    assert win.tabs.tabText(1) == "2・コンテンツを準備する"

    win.ui_lang_combo.setCurrentIndex(win.ui_lang_combo.findData("it"))
    assert win.tabs.tabText(0) == "1 · Importa testi"
    win.close()
    i18n.set_language("it")


def test_ui_language_catalogs_cover_every_key(qapp, temp_workspace: Path) -> None:
    """Ogni lingua elencata ha il suo catalogo e nessuna voce vuota."""
    import json

    from bookwriter.gui import i18n

    codes = {code for code, _ in i18n.UI_LANGUAGES if code != "it"}
    files = {p.stem for p in i18n.LOCALES_DIR.glob("*.json")}
    assert codes == files, codes ^ files
    reference = json.loads((i18n.LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    for code in codes:
        data = json.loads((i18n.LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        assert set(data) == set(reference), f"{code}: chiavi diverse dall'inglese"
        assert all(v.strip() for v in data.values()), f"{code}: traduzioni vuote"


def test_open_file_adds_document_and_selects_it(qapp, temp_workspace: Path, tmp_path: Path) -> None:
    """«Apri un file…» importa il file e lo seleziona subito nella tendina dei documenti."""
    win = MainWindow()
    project = win.pm.create("Progetto apri")
    ppaths = win.pm.paths(project.slug)
    win._refresh_rewrite_lists()
    assert win.rw_doc_combo.count() == 0

    esterno = tmp_path / "capitolo esterno.txt"
    esterno.write_text("Prima frase del capitolo. Seconda frase del capitolo.", encoding="utf-8")

    # Il dialogo di scelta file è sostituito: il resto del percorso è quello vero.
    from PySide6.QtWidgets import QFileDialog

    originale = QFileDialog.getOpenFileNames
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([str(esterno)], ""))
    try:
        win._open_document(win.rw_doc_combo, win._refresh_rewrite_lists)
        # Il lavoro gira in background: lo aspettiamo.
        deadline = time.time() + 30
        while win.rw_doc_combo.count() == 0 and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.05)
    finally:
        QFileDialog.getOpenFileNames = originale

    from bookwriter.db import project_schema

    documenti = project_schema.list_documents(ppaths.db)
    assert len(documenti) == 1
    assert win.rw_doc_combo.count() == 1
    assert win.rw_doc_combo.currentData() == documenti[0]["id"]
    assert "capitolo esterno" in win.rw_doc_combo.currentText()
    win.close()


def test_save_dialog_suggests_a_name_and_remembers_the_folder(qapp, temp_workspace: Path, tmp_path: Path) -> None:
    """Nome proposto e cartella dell'ultimo salvataggio, ricordata anche fra le sessioni."""
    from PySide6.QtWidgets import QFileDialog

    from bookwriter.config import load_global_config

    win = MainWindow()
    project = win.pm.create("Progetto salvataggi")
    ppaths = win.pm.paths(project.slug)

    visti: list[str] = []
    scelto = tmp_path / "altrove" / "libro mio.docx"
    scelto.parent.mkdir()
    originale = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(
        lambda parent, titolo, percorso, filtro="": (visti.append(percorso), (str(scelto), ""))[1]
    )
    try:
        out = win._ask_save_path("quaderno_antologia_IT.docx", ppaths)
    finally:
        QFileDialog.getSaveFileName = originale

    assert out == scelto
    # La prima volta parte dalla cartella exports del progetto, col nome proposto.
    assert visti[0].endswith("quaderno_antologia_IT.docx")
    assert str(ppaths.exports) in visti[0]
    # La cartella scelta è salvata nei default globali: vale anche alla riapertura.
    assert load_global_config().ui.last_export_dir == str(scelto.parent)

    # Seconda chiamata: riparte da lì.
    QFileDialog.getSaveFileName = staticmethod(
        lambda parent, titolo, percorso, filtro="": (visti.append(percorso), ("", ""))[1]
    )
    try:
        annullato = win._ask_save_path("altro.docx", ppaths)
    finally:
        QFileDialog.getSaveFileName = originale
    assert annullato is None  # annullando non si esporta nulla
    assert visti[1] == str(scelto.parent / "altro.docx")
    win.close()


def test_every_screen_that_produces_a_file_lets_you_choose_its_format(qapp, temp_workspace: Path) -> None:
    """DOCX, PDF o Markdown: la scelta c'è ovunque esca un libro, non solo nell'antologia."""
    win = MainWindow()
    tendine = {
        "antologia e prosa": win.format_combo,
        "riscrivi in altro stile": win.rw_format,
        "traduci": win.tr_format,
        "scrivi da zero": win.au_format,
        "aggiorna un libro": win.up_format,
    }
    for schermata, combo in tendine.items():
        valori = [combo.itemData(i) for i in range(combo.count())]
        assert valori == ["docx", "pdf", "md"], schermata
    win.close()


def test_the_buttons_no_longer_promise_docx(qapp, temp_workspace: Path) -> None:
    """Il formato lo sceglie l'utente: prometterlo nel nome del pulsante sarebbe una bugia."""
    from PySide6.QtWidgets import QPushButton

    win = MainWindow()
    etichette = [b.text() for b in win.findChildren(QPushButton)]
    assert not any("DOCX" in e for e in etichette), etichette
    assert any("Traduci tutto il libro" in e for e in etichette)
    win.close()


def _progetto_con_libro(win, nome: str = "Coda") -> tuple[str, object, int]:
    """Un progetto con un documento importato, pronto da mettere in coda."""
    from sqlmodel import Session

    from bookwriter.db.project_schema import Chunk, Document, init_project_db, make_engine

    slug = win.pm.create(nome).slug
    win.pm.set_active(slug)
    ppaths = win.pm.paths(slug)
    init_project_db(ppaths.db)
    with Session(make_engine(ppaths.db)) as ses:
        doc = Document(path="libro.txt", format="txt", sha256="x", title="Il mio libro")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        ses.add(Chunk(document_id=doc.id, book_pos=0, text="Un brano di prosa.", sha256="c0"))
        ses.commit()
        return slug, ppaths, doc.id


def test_a_translation_can_be_queued_with_its_own_file_and_folder(qapp, temp_workspace: Path) -> None:
    """Mettere in fila significa decidere adesso dove finirà quel libro."""
    from PySide6.QtWidgets import QFileDialog

    win = MainWindow()
    _slug, _ppaths, doc_id = _progetto_con_libro(win)
    win._refresh_translate_lists()
    win.tr_lang.setText("inglese")
    win.tr_doc_combo.setCurrentIndex(win.tr_doc_combo.findData(doc_id))
    win.tr_format.setCurrentIndex(win.tr_format.findData("pdf"))

    destinazione = Path(temp_workspace) / "consegne" / "Il mio libro EN.pdf"
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    originale = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(destinazione), ""))
    try:
        win._enqueue_translation()
    finally:
        QFileDialog.getSaveFileName = originale

    assert len(win._queue) == 1
    libro = win._queue[0]
    assert libro.out_path == destinazione and libro.fmt == "pdf"
    assert libro.operation == "traduci in inglese"
    assert win.queue_tree.topLevelItemCount() == 1
    assert win.queue_tree.topLevelItem(0).text(3) == str(destinazione)
    win.close()


def test_the_queue_can_be_reordered_and_emptied(qapp, temp_workspace: Path) -> None:
    """La posizione nella fila decide chi viene lavorato prima: dev'essere modificabile."""
    from bookwriter.batch import QueuedBook

    win = MainWindow()
    _progetto_con_libro(win)
    for n in range(3):
        win._queue.append(QueuedBook(document_id=n, title=f"libro{n}", out_path=Path(f"{n}.md")))
    win._refresh_queue_table()

    win.queue_tree.setCurrentItem(win.queue_tree.topLevelItem(2))
    win._move_in_queue(-1)
    assert [b.title for b in win._queue] == ["libro0", "libro2", "libro1"]

    win.queue_tree.setCurrentItem(win.queue_tree.topLevelItem(0))
    win._remove_from_queue()
    assert [b.title for b in win._queue] == ["libro2", "libro1"]
    assert win.queue_tree.topLevelItemCount() == 2
    win.close()


def test_the_destination_extension_decides_the_format(qapp, temp_workspace: Path) -> None:
    """Salvare un .pdf con dentro un DOCX sarebbe una trappola: comanda l'estensione scelta."""
    from PySide6.QtWidgets import QFileDialog

    from bookwriter.batch import QueuedBook

    win = MainWindow()
    _progetto_con_libro(win)
    win._queue.append(QueuedBook(document_id=1, title="libro", out_path=Path("a.docx"), fmt="docx"))
    win._refresh_queue_table()
    win.queue_tree.setCurrentItem(win.queue_tree.topLevelItem(0))

    nuova = Path(temp_workspace) / "altrove.md"
    originale = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(nuova), ""))
    try:
        win._change_queue_target()
    finally:
        QFileDialog.getSaveFileName = originale

    assert win._queue[0].out_path == nuova and win._queue[0].fmt == "md"
    win.close()


def test_long_jobs_can_be_interrupted(qapp, temp_workspace: Path) -> None:
    """Il pulsante compare solo quando serve, e chiedere di fermarsi alza la bandierina."""
    win = MainWindow()
    assert win.stop_button.isHidden(), "senza lavori in corso non serve"

    win._run_bg(lambda _p: None, lambda _r: None, "Prova…", progressable=True, stoppable=True)
    assert not win.stop_button.isHidden(), "durante un lavoro interrompibile si deve poter premere"
    assert win._stop_requested is False

    win._request_stop()

    assert win._stop_requested is True
    assert not win.stop_button.isEnabled(), "una richiesta basta; il resto è attesa"
    win.close()


def test_every_language_translates_every_visible_string() -> None:
    """Un'interfaccia mezza tradotta è peggio di una non tradotta: si controlla, non si spera."""
    import ast
    import json

    from bookwriter.gui.i18n import LOCALES_DIR, UI_LANGUAGES

    chiavi = set()
    for sorgente in Path("bookwriter/gui").rglob("*.py"):
        for nodo in ast.walk(ast.parse(sorgente.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id == "t":
                if nodo.args and isinstance(nodo.args[0], ast.Constant):
                    chiavi.add(nodo.args[0].value)
    # Le stringhe senza parole (frecce, «PDF», soli segnaposti) sono uguali in ogni lingua:
    # t() restituisce l'originale, che è già quello giusto.
    da_tradurre = {k for k in chiavi if any(c.isalpha() for c in k.replace("PDF", ""))}

    for codice, nome in UI_LANGUAGES:
        if codice == "it":  # è la lingua delle chiavi: non ha catalogo
            continue
        catalogo = json.loads((LOCALES_DIR / f"{codice}.json").read_text(encoding="utf-8"))
        mancanti = sorted(da_tradurre - set(catalogo))
        assert not mancanti, f"{nome} ({codice}) senza traduzione per: {mancanti[:5]}"


def test_the_interrupt_button_follows_the_interface_language(qapp, temp_workspace: Path) -> None:
    """Vive nella barra di stato, fuori dalle schede: senza cura resterebbe l'unica parola italiana."""
    from bookwriter.gui import i18n

    win = MainWindow()
    assert win.stop_button.text() == "Interrompi"
    win.ui_lang_combo.setCurrentIndex(win.ui_lang_combo.findData("en"))
    assert win.stop_button.text() == "Interrupt"
    win.ui_lang_combo.setCurrentIndex(win.ui_lang_combo.findData("it"))
    assert i18n.current_language() == "it"
    win.close()


def test_restarting_the_queue_picks_up_the_interrupted_book(qapp, temp_workspace: Path) -> None:
    """Riprendere un libro fermato a metà è il motivo principale per riavviare la coda."""
    from bookwriter.batch import ANNULLATO, ATTESA, FALLITO, FATTO, INTERROTTO, QueuedBook

    win = MainWindow()
    win._queue.extend([
        QueuedBook(document_id=1, title="consegnato", out_path=Path("a.md"), state=FATTO),
        QueuedBook(document_id=2, title="fermato a metà", out_path=Path("b.md"), state=INTERROTTO),
        QueuedBook(document_id=3, title="mai partito", out_path=Path("c.md"), state=ANNULLATO),
        QueuedBook(document_id=4, title="non riuscito", out_path=Path("d.md"), state=FALLITO),
    ])

    da_fare = win._queue_to_run()

    assert [b.title for b in da_fare] == ["fermato a metà", "mai partito", "non riuscito"]
    assert all(b.state == ATTESA for b in da_fare), "rimessi in attesa, pronti a ripartire"
    assert win._queue[0].state == FATTO, "quello già consegnato non si rifà"
    win.close()


def test_queueing_keeps_you_where_you_are_and_keeps_your_selection(qapp, temp_workspace: Path) -> None:
    """Chi accoda ne accoda parecchi: non deve tornare indietro e ricercare il documento ogni volta."""
    from PySide6.QtWidgets import QFileDialog

    win = MainWindow()
    _slug, _ppaths, doc_id = _progetto_con_libro(win)
    win._refresh_translate_lists()
    win.tr_lang.setText("inglese")
    win.tr_doc_combo.setCurrentIndex(win.tr_doc_combo.findData(doc_id))
    tab_di_partenza = win.tabs.currentIndex()

    originale = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (str(Path(temp_workspace) / "uscita.docx"), "")
    )
    try:
        win._enqueue_translation()
    finally:
        QFileDialog.getSaveFileName = originale

    assert len(win._queue) == 1
    assert win.tabs.currentIndex() == tab_di_partenza, "non si salta sulla coda"
    assert win.tr_doc_combo.currentData() == doc_id, "il documento resta selezionato"
    # E resta selezionato anche tornando sul pannello, che ricostruisce gli elenchi.
    win._refresh_translate_lists()
    assert win.tr_doc_combo.currentData() == doc_id
    win.close()


def test_the_queue_is_reloaded_when_the_project_opens(qapp, temp_workspace: Path) -> None:
    """Riaprendo il programma la fila dev'essere ancora lì, coi suoi file di destinazione."""
    from bookwriter.batch import QueuedBook, save_queue

    win = MainWindow()
    _slug, ppaths, doc_id = _progetto_con_libro(win, "Con coda")
    save_queue(ppaths.db, [
        QueuedBook(document_id=doc_id, title="Il mio libro",
                   out_path=Path(temp_workspace) / "fatto.pdf", fmt="pdf",
                   mode="translate", target_language="inglese"),
    ])

    win._load_queue()  # è quel che accade all'apertura del progetto

    assert [b.title for b in win._queue] == ["Il mio libro"]
    assert win.queue_tree.topLevelItemCount() == 1
    voce = win.queue_tree.topLevelItem(0)
    assert voce.text(1) == "traduci in inglese" and voce.text(2) == "pdf"
    assert voce.text(3).endswith("fatto.pdf")
    win.close()


def test_changing_the_queue_writes_it_to_the_project(qapp, temp_workspace: Path) -> None:
    """Ogni modifica alla coda finisce sul disco: non si perde chiudendo la finestra."""
    from bookwriter.batch import QueuedBook, load_queue

    win = MainWindow()
    _slug, ppaths, doc_id = _progetto_con_libro(win, "Coda salvata")
    win._queue.append(QueuedBook(document_id=doc_id, title="Primo", out_path=Path("a.md")))
    win._queue.append(QueuedBook(document_id=doc_id, title="Secondo", out_path=Path("b.md")))
    win._refresh_queue_table()

    assert [b.title for b in load_queue(ppaths.db)] == ["Primo", "Secondo"]

    win.queue_tree.setCurrentItem(win.queue_tree.topLevelItem(1))
    win._move_in_queue(-1)
    assert [b.title for b in load_queue(ppaths.db)] == ["Secondo", "Primo"], "anche l'ordine"

    win._remove_from_queue()
    assert [b.title for b in load_queue(ppaths.db)] == ["Primo"]
    win.close()
