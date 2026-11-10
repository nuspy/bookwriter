"""Smoke test della GUI: costruzione della finestra in modalità offscreen (nessun display)."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from bookwriter.forge import saves as fg_saves
from bookwriter.gui.main_window import MainWindow
from bookwriter.gui.worker import INTERROTTO
from bookwriter.llm.client import LLMCancelled


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def niente_rete_verso_i_fornitori(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cambiare fornitore nel pannello interroga davvero il suo catalogo dei modelli.

    In un test quella richiesta parte su un thread vero verso un indirizzo vero: sporca la
    macchina di chi esegue la suite e, se torna in ritardo, finisce dentro il finto server
    HTTP del file successivo facendolo fallire senza colpa. Qui i modelli si impostano a
    mano (``win._models``), quindi la lettura dal fornitore non serve a nessun test.
    """
    monkeypatch.setattr(MainWindow, "_load_models",
                        lambda self, *, force=False: self._fill_model_combos())


def test_mainwindow_builds(qapp, temp_workspace: Path) -> None:
    win = MainWindow()
    # Le schede di primo livello sono i lavori; dentro, le loro sotto-schede.
    titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert titles == ["Scrivi libro da appunti", "Scrivi da zero", "Completa un libro",
                      "Aggiorna un libro", "Riscrivi in altro stile", "Traduci",
                      "Stile di scrittura", "Leggi anteprima in lavorazione", "Qualità",
                      "Utilità"]
    sotto = {win.tabs.tabText(i): [st.tabText(j) for j in range(st.count())]
             for i, st in win._subtabs.items()}
    assert sotto == {
        "Scrivi libro da appunti": ["1 · Importa testi", "2 · Prepara contenuti", "3 · Struttura",
                                    "4 · Componi il libro", "Riepilogo progetto"],
        "Completa un libro": ["Completa (a fasi)", "Lacune negli appunti"],
        "Aggiorna un libro": ["Aggiorna (a fasi)", "Integra dagli appunti"],
        "Qualità": ["Un capitolo", "Il libro intero", "Struttura del libro"],
        "Utilità": ["Coda di lavorazione", "Consumi dei modelli", "Impostazioni"],
    }
    assert all(not win.tabs.tabIcon(i).isNull() for i in range(win.tabs.count())), "le icone"
    win.close()


def test_the_phased_panel_serves_three_jobs_each_with_its_own_book(
    qapp, temp_workspace: Path,
) -> None:
    """Da zero, completa, aggiorna: lo stesso pannello a fasi, tre libri separati."""
    win = MainWindow()
    win.pm.create("Tre lavori")
    win._reload_projects()
    for titolo, modo in (("Completa un libro", "completa"), ("Aggiorna un libro", "aggiorna"),
                         ("Scrivi da zero", "nuovo")):
        win.tabs.setCurrentIndex(next(i for i in range(win.tabs.count())
                                      if win.tabs.tabText(i) == titolo))
        assert win._forge_mode == modo
        assert win._forge_page.parentWidget() is win._forge_hosts[modo]
        assert win._forge().project.root.name == {"nuovo": "book"}.get(modo, modo)
        assert win.fg_manuscript.isHidden() == (modo == "nuovo")
    win.close()


def test_day_night_and_auto_themes_keep_text_readable(qapp, temp_workspace: Path) -> None:
    from bookwriter.config import load_global_config
    from bookwriter.gui import theme

    for toni in (theme.LIGHT, theme.DARK):
        assert min(theme.check_contrast(toni).values()) >= 4.5
    win = MainWindow()
    win.theme_combo.setCurrentIndex(win.theme_combo.findData("dark"))
    assert load_global_config().ui.theme == "dark"
    from PySide6.QtGui import QPalette

    assert qapp.palette().color(QPalette.ColorRole.Window).lightness() < 128
    win.theme_combo.setCurrentIndex(win.theme_combo.findData("light"))
    assert qapp.palette().color(QPalette.ColorRole.Window).lightness() > 128
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

    # La chiave finisce nel registro comune, non nel file del progetto: è la stessa credenziale
    # per tutti i progetti, e i file di progetto finiscono nei commit.
    win.set_api_key.setText("chiave-di-prova")
    win._save_settings()
    cfg = win.pm.config_for(win.pm.active_slug())
    assert cfg.llm.provider == "nebius"
    assert cfg.llm.models.reasoning == "Qwen/Qwen3-235B-A22B-Instruct-2507"
    assert cfg.llm.api_key == "", "la chiave non deve restare nel file del progetto"
    assert cfg.llm.resolved_api_key() == "chiave-di-prova", "ma deve continuare a essere usata"

    from llmswitch import Registry

    assert Registry().api_key_for("nebius") == "chiave-di-prova"
    win.close()


def test_a_key_already_in_the_project_file_keeps_working(qapp, temp_workspace: Path) -> None:
    """Chi aveva la chiave nel config.toml non deve accorgersi del cambiamento.

    Il campo la mostra ancora, le richieste la usano ancora, e salvando le impostazioni non
    viene cancellata: toglierla lascerebbe senza accesso chi non ha ancora popolato il registro.
    """
    win = MainWindow()
    win.pm.create("Progetto con chiave vecchia")
    cfg = win.pm.config_for(win.pm.active_slug())
    vecchia = cfg.model_copy(update={
        "llm": cfg.llm.model_copy(update={"provider": "nebius", "api_key": "chiave-nel-file"}),
    })
    win.pm.save_config_for(win.pm.active_slug(), vecchia)

    # Il campo si popola senza passare da `_refresh_settings`, che andrebbe a chiedere
    # l'elenco dei modelli al server: un test non deve dipendere da cosa c'è acceso fuori.
    assert win._chiave_in_uso(vecchia) == "chiave-nel-file"
    assert vecchia.llm.resolved_api_key() == "chiave-nel-file"
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
    assert win._subtabs[0].tabText(0) == "1 · Importa testi"

    win.ui_lang_combo.setCurrentIndex(win.ui_lang_combo.findData("en"))
    assert i18n.current_language() == "en"
    assert win._subtabs[0].tabText(0) == "1 · Import texts"
    ultima = win.tabs.count() - 1
    assert win._subtabs[ultima].tabText(2) == "Settings"
    assert load_global_config().ui.language == "en"  # ricordata per la prossima sessione

    win.ui_lang_combo.setCurrentIndex(win.ui_lang_combo.findData("hu"))
    assert win._subtabs[0].tabText(0) == "1 · Szövegek importálása"
    assert win._subtabs[0].tabText(1) == "2 · Tartalom előkészítése"

    win.ui_lang_combo.setCurrentIndex(win.ui_lang_combo.findData("ja"))
    assert win._subtabs[0].tabText(0) == "1・テキストを取り込む"
    assert win._subtabs[0].tabText(1) == "2・コンテンツを準備する"

    win.ui_lang_combo.setCurrentIndex(win.ui_lang_combo.findData("it"))
    assert win._subtabs[0].tabText(0) == "1 · Importa testi"
    win.close()
    i18n.set_language("it")


def test_ui_language_catalogs_cover_every_key(qapp, temp_workspace: Path) -> None:
    """Ogni lingua elencata ha il suo catalogo e nessuna voce vuota. L'inglese è completo; gli
    altri cataloghi possono avere meno voci (ricadono sull'inglese), mai voci che l'inglese non
    ha."""
    import json

    from bookwriter.gui import i18n

    codes = {code for code, _ in i18n.UI_LANGUAGES if code != "it"}
    files = {p.stem for p in i18n.LOCALES_DIR.glob("*.json")}
    assert codes == files, codes ^ files
    reference = json.loads((i18n.LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    for code in codes:
        data = json.loads((i18n.LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        assert set(data) <= set(reference), f"{code}: chiavi che l'inglese non ha"
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
    """DOCX, PDF, Markdown o EPUB: la scelta c'è ovunque esca un libro, non solo nell'antologia."""
    win = MainWindow()
    tendine = {
        "antologia e prosa": win.format_combo,
        "riscrivi in altro stile": win.rw_format,
        "traduci": win.tr_format,
    }
    for schermata, combo in tendine.items():
        valori = [combo.itemData(i) for i in range(combo.count())]
        assert valori == ["docx", "pdf", "md", "epub"], schermata
    # «Aggiorna un libro» non offre l'EPUB, e per un motivo: quel file serve a rileggere le
    # modifiche marcate, e un libro elettronico non ha né pagine né marcature di revisione.
    assert [win.up_format.itemData(i) for i in range(win.up_format.count())] == ["docx", "pdf", "md"]
    # «Scrivi da zero» non ha una tendina: i formati li sceglie nella fase di composizione,
    # perché lì escono tutti insieme e ciascuno serve a un canale diverso.
    assert (win.fg_pdf.isChecked(), win.fg_docx.isChecked(), win.fg_epub.isChecked()) == (True,) * 3
    win.close()


def _forge_inline(win, monkeypatch) -> None:
    """Esegue in linea il lavoro di sfondo: nei test non c'è un ciclo di eventi da aspettare."""
    def subito(fn, on_done, busy="", progressable=False, on_error=None, on_note=None,
               stoppable=False):
        # Stessa firma che passa il Worker vero: con il racconto in corso la funzione riceve
        # due callback, non una. Se qui ne passassimo sempre una sola, i test direbbero «va
        # tutto bene» mentre la finestra vera si rompe al primo clic.
        def avanzamento(*_a) -> None:
            """Nei test la barra non c'è: l'avanzamento si accetta e si butta."""

        nota = on_note if on_note is not None else avanzamento
        try:
            if progressable and on_note is not None:
                risultato = fn(avanzamento, nota)
            elif progressable:
                risultato = fn(avanzamento)
            else:
                risultato = fn()
            on_done(risultato)
        except Exception as exc:  # gli errori si vedono, non si ingoiano
            if on_error is None:
                raise
            on_error(str(exc))
        except LLMCancelled:  # «Interrompi»: come fa il Worker vero
            if on_error is None:
                raise
            on_error(INTERROTTO)

    monkeypatch.setattr(win, "_run_bg", subito)


def test_the_phases_tab_walks_the_whole_way(qapp, temp_workspace: Path, monkeypatch) -> None:
    """La scheda «Scrivi da zero» porta davvero dal colloquio al libro composto."""
    from tests.test_forge import FintoModello

    win = MainWindow()
    win.pm.create("Sette fasi")
    monkeypatch.setattr(win, "_client", lambda slug: FintoModello())
    _forge_inline(win, monkeypatch)
    win._refresh_forge()

    # Fase 1 — una conversazione: l'autore scrive, la risposta arriva, il quaderno cresce;
    # poi l'autore chiude e il resto lo decide l'assistente.
    win._forge_send("Un giallo a Trieste, d'inverno.")
    bolle = win.fg_chat.view.bubbles()
    assert [b.role for b in bolle] == ["assistant", "user", "assistant"]
    assert bolle[-1].text == FintoModello.RISPOSTA
    assert "Elena Sartori" in win.fg_chat.notebook.toPlainText()
    win._forge_finish_interview()
    forge = win._forge()
    assert forge.state.interview_closed
    assert "Il libro si apre con il corpo nel porto" in win.fg_chat.view.bubbles()[-1].text
    assert win._forge_advance() is None and win._forge().state.phase == "spec"

    # Fasi 2-7, dai pulsanti della scheda.
    win._forge_make_spec()
    win._forge_approve_spec()
    win._forge_decline_research()
    win._forge_write_all()
    win._forge_produce()
    win.fg_no_cover.setChecked(True)
    win.fg_epub.setChecked(False)  # l'EPUB senza copertina è solo un avviso in più
    win._forge_build()
    win._forge_handover()

    forge = win._forge()
    assert sorted(forge.project.written_chapters()) == [1, 2, 3]
    assert forge.project.spec_path.exists() and forge.project.config_path.exists()
    assert forge.project.notes_path.exists()
    assert [f.state for f in forge.phases()] == ["fatta"] * 8
    # Le pastiglie di fase seguono lo stato, non restano quelle di partenza.
    assert win.fg_phase_buttons["build"].styleSheet()
    win.close()


def _scheda_libro(win, monkeypatch, client=None) -> None:
    from tests.test_forge import FintoModello

    win.pm.create("Conversazione")
    modello = client or FintoModello()
    monkeypatch.setattr(win, "_client", lambda slug: modello)
    _forge_inline(win, monkeypatch)
    win._refresh_forge()


def test_the_conversation_opens_with_an_invitation(qapp, temp_workspace: Path, monkeypatch) -> None:
    """Si comincia raccontando il libro, non rispondendo a un modulo."""
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    bolle = win.fg_chat.view.bubbles()
    assert [b.role for b in bolle] == ["assistant"]
    assert "Raccontami il libro" in bolle[0].text
    assert win.fg_center.currentWidget() is win.fg_chat
    # Aprire la scheda non è lavoro: niente da salvare finché l'autore non scrive.
    assert not fg_saves.has_work(win._forge().project)
    win.close()


def test_a_reply_that_does_not_arrive_offers_a_retry(qapp, temp_workspace: Path, monkeypatch) -> None:
    """Il modello non risponde: quello che l'autore ha scritto resta, e «Riprova» lo rimanda."""
    from bookwriter.llm.client import LLMError
    from tests.test_forge import FintoModello

    class UnaVoltaNo(FintoModello):
        caduto = False

        def chat(self, messages, **kw):
            if not self.caduto and "Sei un editor esperto" in messages[0]["content"]:
                self.caduto = True
                raise LLMError("il server non risponde")
            return super().chat(messages, **kw)

    win = MainWindow()
    _scheda_libro(win, monkeypatch, UnaVoltaNo())
    win._forge_send("Un giallo a Trieste.")
    from bookwriter.gui.chat import Notice

    avvisi = win.fg_chat.view.findChildren(Notice)
    assert avvisi and avvisi[-1].button is not None
    assert win._forge().state.transcript[-1]["role"] == "user"

    avvisi[-1].button.click()

    trascritto = win._forge().state.transcript
    assert [b["role"] for b in trascritto] == ["assistant", "user", "assistant"]
    assert win.fg_chat.view.bubbles()[-1].text == FintoModello.RISPOSTA
    win.close()


def test_what_is_written_while_the_assistant_is_busy_waits_its_turn(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    win._chat_busy = True  # l'assistente sta ancora aggiornando il quaderno
    win._forge_send("Il colpevole è il fratello.")
    win._forge_send("E la vittima lo sapeva.")
    assert [testo for _, testo in win._chat_queue] == ["Il colpevole è il fratello.",
                                                     "E la vittima lo sapeva."]
    assert [b.role for b in win.fg_chat.view.bubbles()][-2:] == ["user", "user"], "si vedono subito"

    win._chat_busy = False
    win._forge_chat_next()

    trascritto = win._forge().state.transcript
    assert trascritto[1]["text"] == "Il colpevole è il fratello.\n\nE la vittima lo sapeva."
    assert win._chat_queue == []
    win.close()


def test_a_message_sent_during_another_operation_is_kept_not_lost(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Con un'altra operazione in corso la risposta non può partire: il testo resta dov'è e la
    conversazione non resta «occupata» per sempre."""
    from PySide6.QtWidgets import QMessageBox

    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    win._workers.add(object())  # una traduzione, la coda, una stesura…
    win.fg_chat.composer.input.setPlainText("Un giallo a Trieste.")
    win._forge_send(win.fg_chat.composer.text())
    assert win.fg_chat.composer.text() == "Un giallo a Trieste."
    assert [b.role for b in win.fg_chat.view.bubbles()] == ["assistant"]
    assert not win._chat_busy
    assert win._forge().state.transcript == []
    win._workers.clear()
    win.close()


def test_switching_project_shows_that_project_s_conversation(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    primo = win.pm.active_slug()
    win._forge_send("Un giallo a Trieste.")
    win.tabs.setCurrentIndex(next(i for i in range(win.tabs.count())
                                  if win.tabs.tabText(i) == "Scrivi da zero"))
    secondo = win.pm.create("Un altro libro").slug
    win.pm.set_active(primo)
    win._reload_projects()
    assert win.project_combo.currentData() == primo

    win.project_combo.setCurrentIndex(win.project_combo.findData(secondo))
    assert win.pm.active_slug() == secondo
    assert [b.role for b in win.fg_chat.view.bubbles()] == ["assistant"], "il libro nuovo è vuoto"
    win.project_combo.setCurrentIndex(win.project_combo.findData(primo))
    assert [b.role for b in win.fg_chat.view.bubbles()] == ["assistant", "user", "assistant"]
    win.close()


def test_the_chosen_style_reaches_the_assistant_and_is_remembered(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Lo stile non si chiede: lo dice la tendina, e l'assistente lo conosce."""
    from bookwriter.db import project_schema
    from tests.test_forge import FintoModello

    modello = FintoModello()
    win = MainWindow()
    _scheda_libro(win, monkeypatch, modello)
    db = win.pm.paths(win.pm.active_slug()).db
    stile = project_schema.save_style(db, "Noir asciutto", "abstract",
                                      {"name": "Noir asciutto", "origin": "abstract",
                                       "dimensions": {"ritmo": "frasi brevi, secche"}})
    win._refresh_forge()
    win.fg_style.setCurrentIndex(win.fg_style.findData(stile))

    win._forge_send("Un giallo a Trieste.")

    sistema = modello.conversazioni[-1][0]["content"]
    assert "frasi brevi, secche" in sistema
    assert win._forge().state.style_id == stile
    win.fg_style.setCurrentIndex(0)
    win._refresh_forge()
    assert win.fg_style.currentData() is None, "la tendina rilegge lo stile salvato nel libro"
    win.close()


def test_a_close_request_written_in_the_chat_closes_the_conversation(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """«Puoi iniziare il lavoro»: l'app chiude da sola, e mostra come andare avanti."""
    from bookwriter.gui.chat import Notice
    from tests.test_forge import FintoModello

    modello = FintoModello()
    win = MainWindow()
    _scheda_libro(win, monkeypatch, modello)
    win._forge_send("Un giallo a Trieste.")
    assert not win._forge().state.interview_closed

    modello.chiude = True
    win._forge_send("Puoi iniziare il lavoro. Buon lavoro!")

    forge = win._forge()
    assert forge.state.interview_closed, "senza premere «Ho finito»"
    assert "Il libro si apre con il corpo nel porto" in win.fg_chat.view.bubbles()[-1].text
    avvisi = [a for a in win.fg_chat.view.findChildren(Notice) if a.button is not None]
    assert avvisi and avvisi[-1].button.text() == "Passa alla specifica"
    assert not win._chat_busy

    avvisi[-1].button.click()
    assert win._forge().state.phase == "spec"
    win.close()


def test_a_conversation_the_author_did_not_ask_to_close_stays_open(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    from bookwriter.gui.chat import Notice
    from tests.test_forge import FintoModello

    win = MainWindow()
    _scheda_libro(win, monkeypatch, FintoModello())
    win._forge_send("Un giallo a Trieste.")
    win._forge_send("Parliamo dei personaggi.")
    forge = win._forge()
    assert not forge.state.interview_closed
    assert forge.state.phase == "interview"
    assert not [a for a in win.fg_chat.view.findChildren(Notice) if a.button is not None]
    win.close()


def test_a_close_request_does_not_close_a_book_that_is_no_longer_open(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Se nel frattempo si apre un altro progetto, la chiusura non tocca quello sbagliato."""
    from tests.test_forge import FintoModello

    modello = FintoModello()
    win = MainWindow()
    _scheda_libro(win, monkeypatch, modello)
    primo = win.pm.active_slug()
    win._forge_send("Un giallo a Trieste.")
    modello.chiude = True
    win.pm.create("Un altro libro")  # il progetto attivo cambia prima dell'aggiornamento
    win._forge_update_notebook(primo)
    assert not win._forge_for(primo).state.interview_closed
    assert not win._forge().state.interview_closed
    assert not win._chat_busy
    win.close()


def _intervista_lampo(win) -> None:
    """Una battuta sola, poi «decidi tu il resto»."""
    win._forge_send("Un giallo a Trieste.")
    win._forge_finish_interview()


def test_the_chapter_sources_button_tells_what_it_finds_chapter_by_chapter(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Il pulsante «Cerca le fonti dei capitoli» racconta mentre lavora, capitolo per capitolo.

    Il racconto arriva da un secondo callback, distinto da quello dell'avanzamento. Quando il
    pulsante non lo accettava, premerlo dava un errore sull'arità della funzione invece delle
    fonti: nessun test lo premeva, e il difetto è arrivato fino all'autore.
    """
    from bookwriter.forge import research as rs
    from tests.test_forge import FintoModello

    win = MainWindow()
    win.pm.create("Fonti dei capitoli")
    monkeypatch.setattr(win, "_client", lambda slug: FintoModello())
    monkeypatch.setattr(
        rs, "_sources_from_model",
        lambda *a, **k: {"findings": [{"claim": "Il mare si alza", "detail": "di 3 mm l'anno",
                                       "level": "B", "source": "IPCC (2023)",
                                       "url": "https://esempio.org"}],
                         "open_questions": []},
    )
    _forge_inline(win, monkeypatch)
    win._refresh_forge()
    _intervista_lampo(win)
    win._forge_make_spec()
    win._forge_approve_spec()

    win._forge_chapter_research()
    assert win._forge().state.research_done is False, "le fonti senza il panorama non bastano"
    win._forge_landscape()
    win._forge_chapter_research()

    testo = win.fg_log.toPlainText()
    forge = win._forge()
    assert forge.state.research_done is True
    # Una riga per capitolo, non un solo riepilogo alla fine: è quello che si vede scorrere.
    for numero in (c["number"] for c in forge.state.outline):
        assert f"Capitolo {numero}" in testo
    assert (forge.project.research_dir / "sources.md").exists()
    win.close()


def test_the_phases_tab_says_what_is_missing_instead_of_guessing(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Comporre senza copertina si ferma e lo dice: la copertina la fornisce l'autore."""
    from PySide6.QtWidgets import QMessageBox

    from tests.test_forge import FintoModello

    win = MainWindow()
    win.pm.create("Senza copertina")
    monkeypatch.setattr(win, "_client", lambda slug: FintoModello())
    _forge_inline(win, monkeypatch)
    win._refresh_forge()
    detto: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: detto.append(a[2]) or QMessageBox.StandardButton.Ok))

    from tests.test_forge import _quaderno_pieno

    forge = win._forge()
    _quaderno_pieno(forge.state)
    forge.save()
    win._forge_make_spec()
    win._forge_approve_spec()
    win._forge_write_all()
    win._forge_produce()
    win.fg_no_cover.setChecked(False)
    win._forge_build()

    # senza copertina la composizione riesce, senza finestre che la fermino, e lo dice nel
    # riquadro di stato; la copertina non è stata inventata
    assert not detto
    assert any(forge.project.out_dir.glob("*.pdf"))
    assert "Composto senza copertina" in win.fg_todo.text()
    assert not list(forge.project.out_dir.glob("*cover*"))
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


def test_english_translates_every_visible_string() -> None:
    """Il desktop si mantiene in italiano e inglese (decisione dell'autore, 25/10/2026): ogni
    scritta visibile ha la sua voce inglese. Le altre lingue ricadono sull'inglese."""
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

    assert ("en", "English") in UI_LANGUAGES
    catalogo = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    mancanti = sorted(da_tradurre - set(catalogo))
    assert not mancanti, f"English (en) senza traduzione per: {mancanti[:5]}"


def test_a_string_missing_in_another_language_falls_back_to_english() -> None:
    from bookwriter.gui import i18n

    try:
        i18n.set_language("ja")
        i18n._catalog.pop("Analizza il libro", None)
        assert i18n.t("Analizza il libro") == "Analyse the book"
        assert i18n.t("una chiave che non c'è") == "una chiave che non c'è"
        i18n.set_language("en")
        assert i18n.t("Analizza il libro") == "Analyse the book"
    finally:
        i18n.set_language("it")


def test_the_opening_of_the_conversation_is_translated_everywhere() -> None:
    """L'apertura passa da una costante, non da una stringa scritta dentro t(): il controllo
    sopra non la vede, e un autore inglese si troverebbe accolto in italiano."""
    import json

    from bookwriter.forge.interview import OPENING
    from bookwriter.gui.i18n import LOCALES_DIR, UI_LANGUAGES

    for codice, nome in UI_LANGUAGES:
        if codice == "it":
            continue
        catalogo = json.loads((LOCALES_DIR / f"{codice}.json").read_text(encoding="utf-8"))
        assert catalogo.get(OPENING, "").strip(), f"{nome}: manca l'apertura della conversazione"


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


def test_a_subscription_provider_shows_its_state_instead_of_a_key_field(
    qapp, temp_workspace: Path, monkeypatch
) -> None:
    """Con un abbonamento non c'è nulla da incollare: un campo chiave vuoto ingannerebbe.

    Al suo posto si dice se è collegato e, quando non lo è, il comando che lo collega.
    """
    import llmswitch

    win = MainWindow()
    win.pm.create("Progetto abbonamento")

    monkeypatch.setattr(llmswitch, "stato", lambda nome: {
        "utilizzabile": False, "rinnovabile": False, "scade_fra_ore": None,
        "come_accedere": "codex login",
    })
    win.set_provider.setCurrentIndex(win.set_provider.findData("codex_oauth"))
    assert win.set_api_key.isHidden() is True, "nessuna chiave da chiedere"
    assert win.set_subscription.isHidden() is False
    assert "codex login" in win.set_subscription.text()

    monkeypatch.setattr(llmswitch, "stato", lambda nome: {
        "utilizzabile": True, "rinnovabile": True, "scade_fra_ore": 37.8,
        "come_accedere": "codex login",
    })
    win._apply_provider_ui()
    testo = win.set_subscription.text()
    assert "Collegato" in testo and "37.8" in testo

    # Tornando a un fornitore a chiave, la riga dell'abbonamento sparisce.
    win.set_provider.setCurrentIndex(win.set_provider.findData("deepseek"))
    assert win.set_subscription.isHidden() is True
    assert win.set_api_key.isHidden() is False
    win.close()


def test_the_subscription_line_never_shows_a_token(qapp, temp_workspace: Path) -> None:
    """La riga finisce negli screenshot delle segnalazioni: dentro non ci va una credenziale."""
    win = MainWindow()
    win.pm.create("Progetto riservatezza")
    for nome in ("codex_oauth", "qwen_oauth", "grok_oauth"):
        if win.set_provider.findData(nome) < 0:
            continue
        win.set_provider.setCurrentIndex(win.set_provider.findData(nome))
        testo = win.set_subscription.text()
        assert "Bearer" not in testo and "eyJ" not in testo, nome
    win.close()


def test_closing_the_window_does_not_leave_the_work_running(
    qapp, temp_workspace: Path, monkeypatch
) -> None:
    """Senza questo, chiudere la finestra lascia un processo vivo che traduce di nascosto.

    Chi riapre l'applicazione ne avvia una seconda, e i due si contendono lo stesso modello.
    È successo: nei tempi dei blocchi si vede l'inglese e il francese che si alternano sulla
    stessa scheda, con un blocco ogni mezz'ora invece che ogni pochi minuti.
    """
    from PySide6.QtCore import QThreadPool
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMessageBox

    from bookwriter.gui import main_window as mw

    win = MainWindow()
    win.pm.create("Progetto chiusura")

    # Senza lavori in corso si chiude e basta, senza domande.
    evento = QCloseEvent()
    win.closeEvent(evento)
    assert evento.isAccepted()

    # Con un lavoro in corso si chiede. Non si aspettano thread veri: qui non ce ne sono, e
    # l'uscita forzata va neutralizzata — un test che chiama os._exit ucciderebbe la suite.
    monkeypatch.setattr(QThreadPool, "waitForDone", lambda self, msec=-1: True)
    monkeypatch.setattr(MainWindow, "_esci_a_forza", staticmethod(lambda: None))
    class _Lungo:
        progressable = True   # solo i lavori lunghi fanno chiedere conferma

    win._workers.add(_Lungo())

    monkeypatch.setattr(mw.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    evento = QCloseEvent()
    win.closeEvent(evento)
    assert not evento.isAccepted(), "un «no» deve lasciare la finestra aperta"
    assert win._stop_requested is False, "e non deve fermare niente"

    monkeypatch.setattr(mw.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    evento = QCloseEvent()
    win.closeEvent(evento)
    assert evento.isAccepted()
    assert win._stop_requested is True, "un «sì» chiede l'interruzione prima di uscire"
    assert win._queue_stop is True
    win._workers.clear()


def test_a_stubborn_job_does_not_keep_the_process_alive(qapp, temp_workspace: Path,
                                                        monkeypatch) -> None:
    """Se il lavoro non si ferma in tempo, si esce lo stesso.

    Qt aspetta i thread del pool prima di far terminare il processo: senza uscita forzata,
    chiudere la finestra lascia un programma invisibile che continua a tradurre. A riaprirlo se
    ne avvia un secondo e i due si contendono la scheda — è successo, ed è costato ore.
    """
    from PySide6.QtCore import QThreadPool
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMessageBox

    from bookwriter.gui import main_window as mw

    win = MainWindow()
    win.pm.create("Progetto ostinato")

    class _Lungo:
        progressable = True

    win._workers.add(_Lungo())
    # Il lavoro NON si ferma: waitForDone scade.
    monkeypatch.setattr(QThreadPool, "waitForDone", lambda self, msec=-1: False)
    monkeypatch.setattr(mw.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    uscite = []
    monkeypatch.setattr(MainWindow, "_esci_a_forza", staticmethod(lambda: uscite.append(True)))

    evento = QCloseEvent()
    win.closeEvent(evento)
    assert evento.isAccepted()
    assert uscite == [True], "un lavoro che non si ferma non deve tenere in vita il processo"
    win._workers.clear()


def test_a_job_that_stops_in_time_exits_the_normal_way(qapp, temp_workspace: Path,
                                                       monkeypatch) -> None:
    """Se si ferma da sé non serve forzare niente: si esce per la via normale."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMessageBox

    from bookwriter.gui import main_window as mw

    win = MainWindow()
    win.pm.create("Progetto ubbidiente")

    class _Lungo:
        progressable = True

    win._workers.add(_Lungo())
    monkeypatch.setattr(QThreadPool, "waitForDone", lambda self, msec=-1: True)
    monkeypatch.setattr(mw.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    uscite = []
    monkeypatch.setattr(MainWindow, "_esci_a_forza", staticmethod(lambda: uscite.append(True)))

    win.closeEvent(QCloseEvent())
    assert uscite == [], "nessuna uscita forzata se il lavoro si è fermato"
    win._workers.clear()


# --------------------------------------------------------------------------- #
#  mettere da parte il libro, riprenderlo, ricominciare
# --------------------------------------------------------------------------- #
def _libro_a_meta(win) -> None:
    """Un libro con dentro qualcosa da perdere: due capitoli, la specifica, lo stato."""
    from bookwriter.forge.project import ForgeState

    forge = win._forge()
    progetto = forge.project
    progetto.scaffold()
    progetto.spec_path.write_text("# Specifica\n\n| 1 | L'apertura |\n| 2 | Il secondo |\n",
                                  encoding="utf-8")
    for numero, titolo in ((1, "L'apertura"), (2, "Il secondo")):
        progetto.chapter_path(numero, titolo).write_text(
            f"# Capitolo {numero}: {titolo}\n\n" + " ".join(["parola"] * 500), encoding="utf-8")
    progetto.save_state(ForgeState(phase="writing", spec_approved=True))
    win._refresh_forge()


def test_the_work_can_be_put_aside_and_picked_up_again(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Mettere da parte non interrompe niente: il lavoro prosegue da dove sta."""
    from PySide6.QtWidgets import QInputDialog

    win = MainWindow()
    win.pm.create("Libro da mettere da parte")
    _forge_inline(win, monkeypatch)
    _libro_a_meta(win)

    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("com'era a metà", True)))
    win._forge_save_work()

    salvataggi = fg_saves.list_saves(win.pm.paths(win.pm.active_slug()).saves)
    assert [s.name for s in salvataggi] == ["com'era a metà"]
    assert salvataggi[0].chapters == 2
    assert sorted(win._forge().project.written_chapters()) == [1, 2], "il libro resta al suo posto"
    assert "1 salvataggio" in win.fg_saves_state.text()

    # Il lavoro prosegue e prende una direzione sbagliata: si torna al salvataggio.
    progetto = win._forge().project
    progetto.chapter_path(3, "Da buttare").write_text("# Capitolo 3\n", encoding="utf-8")
    etichetta = salvataggi[0].label()
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: (etichetta, True)))
    win._forge_restore_save()

    assert sorted(win._forge().project.written_chapters()) == [1, 2]
    nomi = [s.name for s in fg_saves.list_saves(win.pm.paths(win.pm.active_slug()).saves)]
    assert any("prima di riprendere" in n for n in nomi), "anche il lavoro sbagliato è recuperabile"
    win.close()


def test_starting_over_keeps_the_book_when_asked_to(qapp, temp_workspace: Path, monkeypatch) -> None:
    """«Salva e ricomincia»: la scheda torna all'intervista e il libro di prima resta ripescabile."""
    win = MainWindow()
    win.pm.create("Libro da riprendere da capo")
    _forge_inline(win, monkeypatch)
    _libro_a_meta(win)
    monkeypatch.setattr(win, "_forge_reset_choice", lambda _forge: "salva")

    win._forge_reset()

    forge = win._forge()
    assert forge.project.written_chapters() == {}, "il libro è azzerato"
    assert forge.state.phase == "interview" and forge.state.transcript == []
    salvataggi = fg_saves.list_saves(win.pm.paths(win.pm.active_slug()).saves)
    assert len(salvataggi) == 1 and salvataggi[0].chapters == 2
    assert "al sicuro" in win.fg_log.toPlainText()
    win.close()


def test_starting_over_throws_the_book_away_only_when_that_is_the_choice(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Cancellare è una cosa che si sceglie: annullando non si perde niente, scegliendo sì."""
    win = MainWindow()
    win.pm.create("Libro da buttare")
    _forge_inline(win, monkeypatch)
    _libro_a_meta(win)

    monkeypatch.setattr(win, "_forge_reset_choice", lambda _forge: "annulla")
    win._forge_reset()
    assert sorted(win._forge().project.written_chapters()) == [1, 2], "annullare non tocca niente"

    monkeypatch.setattr(win, "_forge_reset_choice", lambda _forge: "cancella")
    win._forge_reset()

    forge = win._forge()
    assert forge.project.written_chapters() == {}
    assert forge.state.phase == "interview"
    assert fg_saves.list_saves(win.pm.paths(win.pm.active_slug()).saves) == []
    win.close()


def test_a_failed_save_never_leads_to_a_deletion(qapp, temp_workspace: Path, monkeypatch) -> None:
    """Chi sceglie «salva e ricomincia» ha chiesto di conservarlo: se il salvataggio non riesce,
    cancellare lo stesso sarebbe l'esatto contrario di quello che ha chiesto."""
    from PySide6.QtWidgets import QMessageBox

    win = MainWindow()
    win.pm.create("Libro che non si salva")
    _forge_inline(win, monkeypatch)
    _libro_a_meta(win)
    monkeypatch.setattr(win, "_forge_reset_choice", lambda _forge: "salva")

    def non_ci_riesce(*_a, **_k):
        raise fg_saves.SaveError("il disco è pieno")

    monkeypatch.setattr(fg_saves, "save_work", non_ci_riesce)
    detto: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda _p, _t, testo, *a, **k: detto.append(testo)))

    win._forge_reset()

    assert sorted(win._forge().project.written_chapters()) == [1, 2], "non si cancella niente"
    assert detto and "non l'ho cancellato" in detto[0]
    win.close()


def test_starting_over_on_an_empty_book_asks_nothing(qapp, temp_workspace: Path, monkeypatch) -> None:
    """Un libro mai cominciato non ha niente da perdere: la domanda sarebbe solo un ostacolo."""
    win = MainWindow()
    win.pm.create("Libro mai cominciato")
    _forge_inline(win, monkeypatch)
    win._forge().project.scaffold()  # solo cartelle vuote
    monkeypatch.setattr(win, "_forge_reset_choice",
                        lambda _forge: pytest.fail("non si chiede niente su un libro vuoto"))

    win._forge_reset()

    assert win._forge().state.phase == "interview"
    win.close()


# --------------------------------------------------------------------------- #
#  «Scrivi da zero»: riquadro di stato, comandi di fase, ricerca che parte da sé
# --------------------------------------------------------------------------- #
def _fino_alla_specifica(win) -> None:
    """Intervista lampo, poi alla fase della specifica."""
    _intervista_lampo(win)
    win._forge_advance()


def test_the_advance_button_says_where_it_goes(qapp, temp_workspace: Path, monkeypatch) -> None:
    """Il testo segue la fase corrente; all'ultima il pulsante sparisce."""
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    atteso = {
        "interview": "Passa alla specifica",
        "spec": "Passa alla ricerca",
        "research": "Passa alla revisione",
        "review": "Passa alla stesura",
        "writing": "Passa alla produzione",
        "production": "Passa alla composizione",
        "build": "Passa ai controlli e alla consegna",
    }
    forge = win._forge()
    for fase, testo in atteso.items():
        forge.go_to(fase)
        win._forge_paint(forge)
        assert win.fg_cmds["advance"].text() == testo, fase
        assert not win.fg_cmds["advance"].isHidden(), fase
    forge.go_to("handover")
    win._forge_paint(forge)
    assert win.fg_cmds["advance"].isHidden()
    win.close()


def test_general_commands_are_on_the_left_and_phase_commands_on_the_right(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    from PySide6.QtWidgets import QPushButton, QWidget

    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    sinistra = win.findChild(QWidget, "fg_general_group")
    destra = win.findChild(QWidget, "fg_phase_group")
    assert sinistra is not None and destra is not None
    generali = {b.text() for b in sinistra.findChildren(QPushButton)}
    assert generali == {"Stato su disco", "Leggi la specifica", "Apri la cartella del libro",
                        "Salva il lavoro",
                        "Riprendi un salvataggio…", "Ricomincia da capo"}
    for chiave, bottone in win.fg_cmds.items():
        assert destra.isAncestorOf(bottone), chiave
        assert not sinistra.isAncestorOf(bottone), chiave
        assert bottone.minimumHeight() >= 44, chiave
    # i pannelli di fase stanno anch'essi a destra
    assert destra.isAncestorOf(win.fg_panels)
    win.close()


def test_command_states_follow_the_book(qapp, temp_workspace: Path, monkeypatch) -> None:
    """Un solo calcolo decide consigliato / disponibile / superato."""
    from bookwriter.gui import phase_guide as pg

    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    forge = win._forge()
    forge.go_to("spec")
    stati = pg.command_states(forge)
    assert stati["spec.make"].state == pg.CONSIGLIATO
    assert stati["spec.approve"].state == pg.SUPERATO and stati["spec.approve"].why
    win._forge_paint(forge)
    assert win.fg_cmds["spec.make"].property("cmd_state") == pg.CONSIGLIATO
    assert not win.fg_cmds["spec.approve"].isEnabled()
    assert win.fg_cmds["spec.approve"].toolTip()

    _intervista_lampo(win)
    win._forge_make_spec()
    forge = win._forge()
    stati = pg.command_states(forge)
    assert stati["spec.approve"].state == pg.CONSIGLIATO
    assert stati["spec.make"].state == pg.DISPONIBILE

    forge.state.research_done = True
    stati = pg.command_states(forge)
    assert stati["research.decline"].state == pg.SUPERATO
    forge.go_to("research")
    win._forge_paint(forge)
    assert not win.fg_cmds["research.decline"].isEnabled()
    assert "già fatta" in win.fg_cmds["research.decline"].toolTip()
    win.close()


def test_command_colors_are_readable_in_both_themes() -> None:
    from bookwriter.gui import phase_guide as pg

    for tema, stati in pg.COLORS.items():
        for stato, (sfondo, testo, bordo) in stati.items():
            assert pg.contrast_ratio(testo, sfondo) >= 4.5, (tema, stato)
            assert pg.contrast_ratio(bordo, sfondo) >= 1.2, (tema, stato)
        assert pg.contrast_ratio(pg.FOCUS[tema], stati[pg.DISPONIBILE][0]) >= 3, tema
        assert pg.contrast_ratio(pg.FOCUS[tema], stati[pg.CONSIGLIATO][0]) >= 3, tema


def test_the_status_box_says_what_to_do_in_each_situation(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    assert win.fg_doing.text() == "Fermo, in attesa di te."
    assert "Racconta il libro" in win.fg_todo.text()          # chat vuota

    win._forge_send("Un giallo a Trieste.")
    assert "Tocca a te" in win.fg_todo.text()                 # in attesa dell'autore
    assert "Ho finito: decidi tu il resto" in win.fg_todo.text()

    win._forge_finish_interview()
    assert "Conversazione chiusa" in win.fg_todo.text()
    assert "Passa alla specifica" in win.fg_todo.text()

    win._forge_advance()
    assert "Proponi la struttura" in win.fg_todo.text()
    win._forge_make_spec()
    assert "Approva la specifica" in win.fg_todo.text()       # specifica da approvare
    assert win.fg_doing.text() == "Fermo, in attesa di te."
    win.close()


def test_the_status_box_shows_the_work_in_progress_and_clears_it(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    visto: dict[str, str] = {}

    def lavoro(avanza):
        visto["prima"] = win.fg_doing.text()
        visto["todo"] = win.fg_todo.text()
        win._bg_hook(1, 4)  # ciò che _run_bg fa quando arriva l'avanzamento vero
        visto["dopo"] = win.fg_doing.text()
        return 1

    win._forge_job(lavoro, lambda r: None, "Prova…", progressable=True,
                   activity="Sto cercando le fonti dei capitoli…",
                   steps="Sto cercando le fonti: capitolo {0} di {1}…", kind="research")
    assert visto["prima"].startswith("● Sto cercando le fonti dei capitoli…")
    assert "0 s" in visto["prima"]
    assert "Aspetta: la ricerca è in corso" in visto["todo"]
    assert "capitolo 2 di 4" in visto["dopo"]
    assert win.fg_doing.text() == "Fermo, in attesa di te."
    assert win._fg_activity is None and not win._fg_timer.isActive()
    win.close()


def test_a_failed_job_is_told_in_the_status_box(qapp, temp_workspace: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    finestre: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: finestre.append(a[2])))

    def guasto():
        raise RuntimeError("Il server non risponde")

    win._forge_job(guasto, lambda r: None, "Prova…", activity="Sto provando…")
    assert "Il server non risponde" in win.fg_todo.text()
    assert "Puoi riprovare" in win.fg_todo.text()
    assert win.fg_doing.text() == "Fermo, in attesa di te."
    assert finestre == ["Il server non risponde"], "la finestra c'è ancora"
    # cambiare fase toglie l'errore
    win._forge_go("interview")
    assert "Il server non risponde" not in win.fg_todo.text()
    win.close()


def test_research_starts_by_itself_and_warns_when_the_web_is_off(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Approvata la specifica la ricerca parte da sola, anche col web spento, e lo dice."""
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    _fino_alla_specifica(win)
    win._forge_make_spec()
    forge = win._forge()
    assert not forge.state.research_done
    assert not (forge.project.research_dir / "00_landscape.md").exists()

    win._forge_approve_spec()

    forge = win._forge()
    assert forge.state.phase == "research"
    assert (forge.project.research_dir / "00_landscape.md").exists(), "il panorama"
    for capitolo in forge.state.outline:
        assert list(forge.project.research_dir.glob(f"{capitolo['number']:02d}_*.md")), "le fonti"
    assert forge.state.research_done
    assert "Il web è spento" in win.fg_todo.text()
    assert "Ricerca finita" in win.fg_todo.text()
    assert not win.fg_status_settings.isHidden(), "il pulsante che porta alle Impostazioni"
    assert not win.fg_status_redo.isHidden(), "«Rifai la ricerca»"
    # il pulsante porta alla scheda Impostazioni
    win._forge_open_settings()
    assert win.tabs.tabText(win.tabs.currentIndex()) == "Utilità"
    sotto = win._subtabs[win.tabs.currentIndex()]
    assert sotto.tabText(sotto.currentIndex()) == "Impostazioni"
    win.close()


def test_research_does_not_restart_when_it_is_already_done_or_declined(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    _fino_alla_specifica(win)
    win._forge_make_spec()
    win._forge_approve_spec()          # la ricerca parte e finisce
    partenze: list[int] = []
    monkeypatch.setattr(win, "_forge_research_all", lambda **k: partenze.append(1))

    win._forge_go("spec")
    win._forge_go("research")          # già fatta: non riparte
    assert partenze == []

    forge = win._forge()
    forge.state.research_done = False
    forge.state.research_declined = True
    forge.save()
    win._forge_go("spec")
    win._forge_go("research")          # rinunciata: non riparte
    assert partenze == []

    forge = win._forge()
    forge.state.research_declined = False
    forge.save()
    win._forge_go("spec")
    win._forge_go("research")          # né fatta né rinunciata: riparte
    assert partenze == [1]
    win.close()


def test_the_other_phases_do_not_start_by_themselves(qapp, temp_workspace: Path, monkeypatch) -> None:
    """Stesura e composizione chiedono scelte (stile, formati, copertina): niente avvio da soli."""
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    _fino_alla_specifica(win)
    win._forge_make_spec()
    win._forge_approve_spec()
    win._forge_advance()               # alla revisione (facoltativa: non parte da sola)
    forge = win._forge()
    assert forge.state.phase == "review"
    assert win._fg_activity is None
    win._forge_advance()               # alla stesura
    forge = win._forge()
    assert forge.state.phase == "writing"
    assert not forge.project.written_chapters()
    assert win._fg_activity is None
    win.close()


def test_an_interrupted_research_is_not_done_and_offers_to_be_redone(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """«Fatta» = panorama e fonti di tutti i capitoli, senza interruzioni."""
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    _fino_alla_specifica(win)
    win._forge_make_spec()
    win._stop_requested = True           # l'autore preme «Interrompi» subito
    win._forge_approve_spec()            # la ricerca parte da sola e si ferma

    forge = win._forge()
    assert forge.state.phase == "research"
    assert not forge.state.research_done
    assert not (forge.project.research_dir / "00_landscape.md").exists()

    partenze: list[int] = []
    originale = win._forge_research_all
    monkeypatch.setattr(win, "_forge_research_all", lambda **k: partenze.append(1))
    win._forge_go("spec")
    win._forge_go("research")
    assert partenze == [], "dopo un'interruzione non si riparte da soli"

    monkeypatch.setattr(win, "_forge_research_all", originale)
    win._stop_requested = False
    win._forge_research_all()
    assert win._forge().state.research_done
    win.close()


def test_the_markdown_checkbox_is_part_of_the_build(qapp, temp_workspace: Path, monkeypatch) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    forge = win._forge()
    _fino_alla_specifica(win)
    win._forge_make_spec()
    win._forge_approve_spec()
    win._forge_decline_research()
    win._forge_write_all()
    win._forge_produce()
    assert win.fg_md.text() == "Markdown (.md)" and win.fg_md.isChecked()
    for casella in (win.fg_pdf, win.fg_docx, win.fg_epub):
        casella.setChecked(False)
    win._forge_build()
    assert list(win._forge().project.out_dir.glob("*.md"))
    win.close()


def test_the_status_box_shows_the_section_being_written(qapp, temp_workspace: Path, monkeypatch) -> None:
    """«Sto scrivendo il capitolo 4 di 15 · sezione 3 di 6»: da dove ripartirebbe, e dove siamo."""
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    visto: dict[str, str] = {}

    def lavoro(avanza, nota):
        win._bg_hook(3, 15)
        nota(("section", 4, 3, 6))
        visto["doing"] = win.fg_doing.text()
        return None

    win._forge_job(lavoro, lambda r: None, "Prova…", progressable=True,
                   activity="Sto scrivendo i capitoli…",
                   steps="Sto scrivendo il capitolo {0} di {1}…",
                   on_note=lambda n: win._forge_note_detail(n))
    assert "capitolo 4 di 15" in visto["doing"] or "capitolo 4 di" in visto["doing"]
    assert "sezione 3 di 6" in visto["doing"]
    win.close()


def test_the_redo_short_command_asks_first_and_moves_chapters_aside(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from tests.test_forge import FintoModello

    win = MainWindow()
    win.pm.create("Capitoli corti")
    monkeypatch.setattr(win, "_client", lambda slug: FintoModello(chapters=3, words=900))
    _forge_inline(win, monkeypatch)
    win._refresh_forge()
    _intervista_lampo(win)
    win._forge_advance()
    win._forge_make_spec()
    win._forge_approve_spec()
    win._forge_decline_research()
    win._forge_write_all()
    forge = win._forge()
    forge.project.written_chapters()[2].write_text("# Capitolo 2: x\n\npoche parole\n",
                                                   encoding="utf-8")
    win._forge_paint(forge)
    assert win.fg_cmds["writing.redo_short"].isEnabled()
    assert "Riscrivi i capitoli troppo corti" in win.fg_todo.text()

    chieste: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: chieste.append(a[2]) or QMessageBox.StandardButton.No))
    win._forge_redo_short()
    assert chieste and "Capitolo 2" in chieste[0]
    assert forge.short_chapters(), "con il no non si tocca niente"

    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes))
    win._forge_redo_short()
    assert not win._forge().short_chapters()
    assert list((forge.project.chapters_dir / ".superati").rglob("*.md"))
    win.close()


# --------------------------------------------------------------------------- #
#  Parte B: l'esploratore della ricerca e la chat delle questioni aperte
# --------------------------------------------------------------------------- #
def _libro_con_ricerca(win, monkeypatch, *, web: bool = True, risolve: str = "mai"):
    """Un libro arrivato alla fase Ricerca, con la ricerca fatta (modello e rete finti)."""
    from bookwriter.forge import research as rs
    from bookwriter.forge.websearch import Page, SearchResult
    from tests.test_research_depth import FintoRicerca

    win.pm.create("Esploratore")
    slug = win.pm.active_slug()
    cfg = win.pm.config_for(slug)
    cfg.research.enabled = web
    win.pm.save_config_for(slug, cfg)
    modello = FintoRicerca()
    modello.risolve_se = risolve
    modello.giri = [["a"], ["b"], ["c"]]
    monkeypatch.setattr(win, "_client", lambda s: modello)
    _forge_inline(win, monkeypatch)

    def gather(query, settings, *, read=None, page=0, skip=None):
        s = "-".join(query.lower().split())[:40]
        return ([SearchResult(f"Titolo {s}", f"https://esempio.org/{s}", "estratto")],
                [Page(f"https://esempio.org/{s}", f"Pagina {s}", f"Testo di {query}")])

    monkeypatch.setattr(rs, "gather", gather)
    win._refresh_forge()
    _intervista_lampo(win)
    win._forge_advance()
    win._forge_make_spec()
    win._forge_approve_spec()      # la ricerca parte da sola
    return modello


def _questioni(win):
    forge = win._forge()
    return forge, forge.pending_questions()


def test_the_explorer_has_three_tabs_and_a_tab_per_chapter(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _libro_con_ricerca(win, monkeypatch)
    forge = win._forge()
    assert win.fg_center.currentWidget() is win.fg_explorer
    tabs = win.fg_explorer.tabs
    assert [tabs.tabText(i).split(" (")[0] for i in range(3)] == [
        "Cosa esiste già", "Fonti dei capitoli", "Questioni aperte"]
    assert win.fg_explorer.chapters_tabs.count() == len(forge.state.outline)
    pagina = win.fg_explorer.page(1)
    assert len(pagina.boxes) == len(forge.state.outline[0]["points"])
    assert "Scena 1 del capitolo 1" in pagina.boxes[0].title()
    assert pagina.boxes[0].rows, "risultati e pagine lette del punto"
    assert "Un libro che c'è" in win.fg_explorer.landscape_view.toPlainText()
    # le questioni da decidere sono nel titolo della terza scheda e nel riquadro di stato
    assert "(" in tabs.tabText(2)
    assert not win.fg_status_questions.isHidden()
    assert "contrasti da discutere" in win.fg_todo.text()
    win.close()


def test_include_and_extend_work_from_the_explorer(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    from bookwriter.forge import research as rs
    from bookwriter.forge import research_store as store
    from bookwriter.forge.websearch import Page

    win = MainWindow()
    _libro_con_ricerca(win, monkeypatch)
    forge = win._forge()
    dati = forge.chapter_data(1)
    nuovo = "https://altro.org/non-letto"
    dati.points[0].results.append(store.Hit("Non letto", nuovo, "solo l'estratto"))
    store.save_chapter(forge.project.research_dir, dati)
    monkeypatch.setattr(rs, "fetch", lambda url, settings: Page(url, "Letto ora", "Testo intero"))
    win._forge_explorer_refresh()

    riga = next(r for r in win.fg_explorer.page(1).boxes[0].rows if r.url == nuovo)
    assert riga.include is not None and riga.include.isEnabled()
    assert riga.state.text() == "non letta"
    riga.include.click()
    assert nuovo in win._forge().chapter_data(1).points[0].read_urls()
    riga = next(r for r in win.fg_explorer.page(1).boxes[0].rows if r.url == nuovo)
    assert riga.include is None and riga.state.text() == "letta"
    assert riga.read_button is not None
    assert riga.excerpt.toPlainText() == "Testo intero"

    prima = len(win._forge().chapter_data(1).points[0].results)
    win.fg_explorer.page(1).boxes[0].extend.click()
    assert len(win._forge().chapter_data(1).points[0].results) > prima
    win.close()


def test_the_explorer_commands_are_off_with_the_web_off_and_say_why(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _libro_con_ricerca(win, monkeypatch)
    monkeypatch.setattr(win, "_forge_web_on", lambda: False)
    win._forge_explorer_refresh()
    pagina = win.fg_explorer.page(1)
    assert not pagina.boxes[0].extend.isEnabled()
    assert "spenta" in pagina.boxes[0].extend.toolTip()
    assert pagina.boxes[0].extend.minimumHeight() >= 44
    for r in [r for b in pagina.boxes for r in b.rows if r.include is not None]:
        assert not r.include.isEnabled() and "spenta" in r.include.toolTip()
    win.close()


def test_the_questions_chat_lists_the_pending_questions_first(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _libro_con_ricerca(win, monkeypatch)
    forge, aperte = _questioni(win)
    assert aperte
    prima = win.fg_explorer.questions.view.bubbles()[0].text
    assert "contrasti" in prima and aperte[0].text in prima
    assert "resta come l'hai scritto" in prima or "restano come li hai scritti" in prima
    assert aperte[0].text in win.fg_explorer.questions.notebook.toPlainText()
    assert win.fg_explorer.questions.composer.attach is not None
    win.close()


def test_a_pasted_url_in_the_chat_resolves_a_question(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    from bookwriter.forge import questions as qs
    from bookwriter.forge.websearch import Page

    win = MainWindow()
    modello = _libro_con_ricerca(win, monkeypatch)
    modello.risolve_se = "archivio"
    monkeypatch.setattr(qs, "fetch", lambda url, settings: Page(url, "Archivio", "Testo."))
    prima = len(_questioni(win)[1])

    win._questions_send("Guarda https://esempio.gov/archivio per favore.")

    forge, dopo = _questioni(win)
    assert len(dopo) < prima
    assert any(q.status == "risolta" for q in forge.questions())
    chat = win.fg_explorer.questions
    assert [b.role for b in chat.view.bubbles()][-2:] == ["user", "assistant"]
    assert win._qchat_busy is False
    win.close()


def test_an_attached_document_in_the_chat_resolves_a_question(
    qapp, temp_workspace: Path, monkeypatch, tmp_path: Path,
) -> None:
    win = MainWindow()
    modello = _libro_con_ricerca(win, monkeypatch)
    modello.risolve_se = "nota"
    documento = tmp_path / "nota.txt"
    documento.write_text("Le vittime furono dodici, dall'archivio di stato del 1978.",
                         encoding="utf-8")
    composer = win.fg_explorer.questions.composer
    composer.add_attachments([documento])
    assert "nota.txt" in composer.attached_label.text() and not composer.attached_row.isHidden()
    prima = len(_questioni(win)[1])

    win._questions_send("")          # solo l'allegato, nessun testo

    forge, dopo = _questioni(win)
    assert len(dopo) < prima
    assert composer.take_attachments() == [], "gli allegati partono e si svuotano"
    utente = [b for b in win.fg_explorer.questions.view.bubbles() if b.role == "user"][-1]
    assert "nota.txt" in utente.text
    win.close()


def test_publish_and_omit_decisions_made_in_the_chat_are_recorded(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    modello = _libro_con_ricerca(win, monkeypatch)
    forge, aperte = _questioni(win)
    primo = aperte[0]
    modello.decisioni = [{"question_id": primo.id, "action": "publish",
                          "text": "Secondo la tradizione, le vittime furono molte."}]

    win._questions_send("Va bene la tua formulazione.")

    forge = win._forge()
    q = next(x for x in forge.questions() if x.id == primo.id)
    assert q.status == "decisa" and q.outcome["kind"] == "publish"
    assert "Secondo la tradizione" in q.outcome["text"]
    assert "si pubblica così" in win.fg_explorer.questions.notebook.toPlainText()

    ancora = forge.pending_questions()
    if ancora:
        modello.decisioni = [{"question_id": ancora[0].id, "action": "omit", "text": ""}]
        win._questions_send("Questa tralasciamola.")
        omessa = next(x for x in win._forge().questions() if x.id == ancora[0].id)
        assert omessa.status == "decisa" and omessa.outcome["kind"] == "omit"
    # la stesura riceve le decisioni
    capitolo = next(c for c in forge.state.outline if int(c["number"]) == primo.chapter)
    assert "scrivi così" in win._forge()._decisions_for(capitolo)
    win.close()


def test_a_url_with_the_web_off_is_said_in_the_chat(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    from PySide6.QtWidgets import QLabel

    from bookwriter.gui.chat import Notice

    win = MainWindow()
    _libro_con_ricerca(win, monkeypatch)
    monkeypatch.setattr(win, "_forge_web_on", lambda: False)
    slug = win.pm.active_slug()
    cfg = win.pm.config_for(slug)
    cfg.research.enabled = False
    win.pm.save_config_for(slug, cfg)
    win._questions_send("Vedi https://esempio.gov/x")
    testi = [" ".join(label.text() for label in n.findChildren(QLabel))
             for n in win.fg_explorer.questions.view.findChildren(Notice)]
    assert any("spenta" in testo for testo in testi), testi
    win.close()


def test_chapter_sub_tabs_carry_number_and_short_title(qapp, temp_workspace: Path, monkeypatch) -> None:
    from bookwriter.gui.research_view import ResearchExplorer

    explorer = ResearchExplorer()
    outline = [{"number": 1, "title": "Il porto"},
               {"number": 2, "title": "Un titolo davvero molto molto lungo per una scheda"}]
    explorer.set_data(None, outline, [], web_on=False)
    tabs = explorer.chapters_tabs
    assert tabs.tabText(0) == "1 · Il porto"
    assert tabs.tabText(1).startswith("2 · Un titolo") and tabs.tabText(1).endswith("…")
    assert len(tabs.tabText(1)) <= 28
    assert "molto lungo per una scheda" in tabs.tabToolTip(1)


def test_the_questions_intro_uses_correct_singular_and_plural(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    from bookwriter.forge import research_store as store

    win = MainWindow()
    win.pm.create("Plurali")
    uno = store.Question("a", "Una cifra", 1, 0, store.CHIESTA, [store.Attempt(1, ["x"], 1, 1)])
    molte = store.Question("b", "Un'altra", 1, 0, store.CHIESTA, [store.Attempt(1, ["x", "y"], 5, 3)])
    testo = win._questions_intro([uno, molte])
    assert "provato con 1 interrogazione, 1 risultato visto." in testo
    assert "provato con 2 interrogazioni, 5 risultati visti." in testo
    assert "1 interrogazioni" not in testo and "1 risultati" not in testo
    win.close()


def test_the_composer_placeholder_is_readable_in_both_themes(qapp) -> None:
    from PySide6.QtGui import QColor, QPalette

    from bookwriter.gui import phase_guide as pg
    from bookwriter.gui.chat import Composer, muted_color

    anteriore = QApplication.palette()
    try:
        for fondo, testo in (("#ffffff", "#202124"), ("#17181a", "#e8eaed")):
            p = QPalette()
            p.setColor(QPalette.ColorRole.Base, QColor(fondo))
            p.setColor(QPalette.ColorRole.Text, QColor(testo))
            QApplication.setPalette(p)
            composer = Composer()
            colore = muted_color(composer)           # «rgb(r,g,b)»
            r, g, b = (int(x) for x in colore[4:-1].split(","))
            assert pg.contrast_ratio(f"#{r:02x}{g:02x}{b:02x}", fondo) >= 4.5, (fondo, colore)
            assert "placeholder-text-color" in composer.input.styleSheet()
    finally:
        QApplication.setPalette(anteriore)


def test_a_research_done_in_the_old_format_does_not_restart_by_itself(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Una ricerca fatta nel formato di prima (solo .md) è un lavoro concluso: entrando in Ricerca
    non riparte da sola (regola dell'autore, 25/10/2026); la rifà «Rifai la ricerca»."""
    win = MainWindow()
    _libro_con_ricerca(win, monkeypatch)
    forge = win._forge()
    rd = forge.project.research_dir
    for json_file in rd.glob("*.json"):
        json_file.unlink()                       # com'era prima: restano i .md
    assert forge.research_format_old()
    partenze: list[int] = []
    originale = win._forge_research_all
    monkeypatch.setattr(win, "_forge_research_all", lambda **k: partenze.append(1))
    win._forge_go("spec")
    win._forge_go("research")
    assert partenze == [], "una ricerca conclusa non riparte da sola"

    monkeypatch.setattr(win, "_forge_research_all", originale)
    win._forge_research_all(mode="redo")         # «Rifai la ricerca»
    forge = win._forge()
    assert forge.landscape_data() is not None, "anche il panorama, che era solo .md"
    assert forge.chapter_data(1) is not None and forge.state.research_done
    assert not forge.research_format_old()
    win.close()


def test_research_starts_by_itself_only_after_the_spec_is_concluded_or_skipped(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Il primo avvio da solo vuole la fase prima conclusa: specifica approvata, o chiusa a mano
    dall'autore («passa avanti lo stesso»). Guardare la fase Ricerca non basta."""
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    _fino_alla_specifica(win)
    win._forge_make_spec()                       # scritta, non approvata
    partenze: list[int] = []
    monkeypatch.setattr(win, "_forge_research_all", lambda **k: partenze.append(1))
    win._forge_go("research")                    # l'autore guarda la fase: non parte
    assert partenze == []
    forge = win._forge()
    assert not forge.can_autostart_research()

    win._forge_go("spec")
    monkeypatch.setattr("bookwriter.gui.main_window.QMessageBox.question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    forge = win._forge()
    forge.state.spec_approved = False
    forge.save()
    win._forge_advance()                         # manca l'approvazione: «passa avanti lo stesso»
    forge = win._forge()
    assert forge.state.phase == "research" and "spec" in forge.state.skipped
    assert partenze == [1], "la specifica chiusa a mano vale come conclusa"
    win.close()


def test_a_new_structure_reuses_the_landscape_and_redoes_only_the_chapter_research(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    from bookwriter.forge.engine import Forge

    win = MainWindow()
    _libro_con_ricerca(win, monkeypatch)
    forge = win._forge()
    assert forge.state.research_done
    testo = forge.project.spec_path.read_text(encoding="utf-8").replace(
        "Il passaggio 2", "Un secondo capitolo diverso")
    forge.save_spec(testo)
    assert not win._forge().state.research_done

    chiamate: list[int] = []
    originale = Forge.research_landscape

    def conta(self, **k):
        chiamate.append(1)
        return originale(self, **k)

    monkeypatch.setattr(Forge, "research_landscape", conta)
    win._forge_go("spec")
    win._forge_go("research")

    forge = win._forge()
    assert chiamate == [], "il panorama c'è già: non si rifà"
    assert forge.state.research_done
    assert forge.chapter_data(2) is not None and forge.state.outline[1]["title"] == "Un secondo capitolo diverso"
    win.close()


def test_the_editorial_reports_list_the_chapters_to_review_first(tmp_path: Path) -> None:
    """Il pulsante dei resoconti non apre una cartella muta: elenca i capitoli, quelli da
    rivedere in testa, con il resoconto e il piano a un clic."""
    from types import SimpleNamespace

    from bookwriter.forge import editor as ed
    from bookwriter.forge.project import BookProject, ForgeState, status

    progetto = BookProject(tmp_path / "book")
    progetto.chapters_dir.mkdir(parents=True)
    progetto.editorial_dir.mkdir(parents=True)
    progetto.plans_dir.mkdir(parents=True)
    for nome, titolo in (("01_inizio", "Inizio"), ("02_scavo", "Scavo"), ("03_corte", "Corte")):
        (progetto.chapters_dir / f"{nome}.md").write_text(f"# {titolo}\n\nUno due tre.",
                                                          encoding="utf-8")
    (progetto.editorial_dir / "01_inizio.md").write_text(
        ed.EditorialReport(chapter=1).markdown(), encoding="utf-8")
    (progetto.editorial_dir / "02_scavo.md").write_text(
        ed.EditorialReport(chapter=2, failed_critical=["protagonista attivo"]).markdown(),
        encoding="utf-8")
    (progetto.plans_dir / "02_scavo.json").write_text("{}", encoding="utf-8")
    stato = ForgeState()
    stato.outline = [{"number": 1, "title": "Inizio"}, {"number": 2, "title": "Scavo"},
                     {"number": 3, "title": "Corte"}]
    forge = SimpleNamespace(project=progetto, state=stato, status=lambda: status(progetto))

    testo = MainWindow._forge_reports_markdown(None, forge)
    righe = [r for r in testo.splitlines() if r.startswith("- **")]
    assert [r[4:6] for r in righe] == ["2.", "1.", "3."]
    assert "protagonista attivo" in righe[0] and "02_scavo.json" in righe[0]
    assert "02_scavo.md" in righe[0] and righe[0].count("file:") == 3
    assert "senza resoconto" in righe[2]


def test_a_running_translation_asks_the_author_through_the_window(qapp, tmp_path, monkeypatch) -> None:
    """La domanda sui modi di dire parte dal thread di lavoro, si apre nella finestra e la
    risposta torna al lavoro; con «Interrompi» il lavoro non resta appeso alla domanda."""
    import threading

    from PySide6.QtCore import QCoreApplication

    from bookwriter.gui import main_window as mw

    monkeypatch.setenv("BOOKWRITER_HOME", str(tmp_path))
    win = mw.MainWindow()
    viste: list[str] = []

    def finestra(voce, *, later=True):
        viste.append(voce["originale"])
        assert threading.current_thread() is threading.main_thread()
        return 1

    monkeypatch.setattr(win, "_idiom_dialog", finestra)
    risposte: list[object] = []
    lavoro = threading.Thread(target=lambda: risposte.append(
        win._decide_idiom({"originale": "in bocca al lupo", "opzioni": []})))
    lavoro.start()
    for _ in range(200):
        QCoreApplication.processEvents()
        if not lavoro.is_alive():
            break
        lavoro.join(0.02)
    assert risposte == [1] and viste == ["in bocca al lupo"]
    # «Non chiedere più»: la traduzione continua senza aprire altre finestre
    win._idioms_silenced = True
    assert win._decide_idiom({"originale": "x", "opzioni": []}) is None and len(viste) == 1
    win._idioms_silenced = False
    win._stop_requested = True
    assert win._decide_idiom({"originale": "y", "opzioni": []}) is None
    win.close()


def test_the_structure_tab_lists_proposals_and_asks_a_waiver_for_blockers(
        qapp, tmp_path, monkeypatch) -> None:
    """La scheda della struttura mostra le proposte; una proposta con blocker si accetta solo
    con una deroga scritta dall'autore."""
    from types import SimpleNamespace

    from bookwriter.gui import main_window as mw

    monkeypatch.setenv("BOOKWRITER_HOME", str(tmp_path))
    win = mw.MainWindow()
    op = SimpleNamespace(id="S1-01", kind="reorder", status="proposta", issue="il 5 prima del 4",
                         evidence="", reason="", risk="", breaks=[], blockers=["usa un nome"],
                         warnings=[], unique_content=[], waiver="", destination=0,
                         line=lambda: "Sposta il capitolo 5 dopo il 3")
    decisioni: list[tuple] = []
    finto = SimpleNamespace(
        structure_data=lambda: SimpleNamespace(operations=lambda: [op], recheck=[4],
                                               integrations=[], rounds=[1]),
        structure_decide=lambda i, d, **kw: decisioni.append((i, d, kw)))
    monkeypatch.setattr(win, "_structure_forge", lambda **kw: finto)
    monkeypatch.setattr(win, "_quality_sources", lambda: [("Il libro", "nuovo")])
    win._refresh_structure()
    assert win.qs_table.topLevelItemCount() == 1 and "4" in win.qs_status.text()
    win.qs_table.setCurrentItem(win.qs_table.topLevelItem(0))
    assert "usa un nome" in win.qs_detail.toPlainText()
    monkeypatch.setattr(mw.QInputDialog, "getMultiLineText",
                        staticmethod(lambda *a, **k: ("", True)))
    win._structure_decide("accept")
    assert not decisioni, "senza motivo la deroga non vale"
    monkeypatch.setattr(mw.QInputDialog, "getMultiLineText",
                        staticmethod(lambda *a, **k: ("il flashback è voluto", True)))
    win._structure_decide("accept")
    assert decisioni == [("S1-01", "accept", {"waiver": "il flashback è voluto",
                                              "destination": None})]
    win.close()
