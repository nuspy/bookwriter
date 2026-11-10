"""L'esploratore della ricerca: cosa si è trovato, punto per punto, e cosa resta da decidere.

Tre schede al centro della fase Ricerca:

* **Cosa esiste già** — il panorama: le opere simili da cui attingere (idee, immagini, scene,
  fonti), e quello che si è cercato per arrivarci;
* **Fonti dei capitoli** — una scheda per capitolo. In ciascuna l'essenziale (cosa fa, i punti
  chiave) e, punto per punto, il materiale trovato, i risultati e le pagine lette, da
  aprire e da leggere. Accanto a ogni risultato non letto «Includi nella ricerca»; per ogni
  punto «Estendi la ricerca». Senza il web questi due comandi sono spenti, e il motivo è
  scritto nel suggerimento;
* **Questioni aperte** — la conversazione con l'assistente sui contrasti che la ricerca ha
  segnalato (il libro resta com'è scritto finché non decidi) (vedi :mod:`bookwriter.forge.questions`).

Il widget mostra dati e lancia richieste (segnali): a leggere pagine e a chiamare il modello
ci pensa la finestra, con i suoi lavori di sfondo.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from bookwriter.forge import research_store as store
from bookwriter.gui.chat import ChatPanel, link_style, muted_color, recolor_links
from bookwriter.gui.i18n import t


def status_words() -> dict[str, str]:
    """Gli stati di una questione, nella lingua dell'interfaccia."""
    return {
        store.APERTA: t("da approfondire"),
        store.IN_APPROFONDIMENTO: t("in approfondimento"),
        store.RISOLTA: t("risolta"),
        store.CHIESTA: t("da decidere"),
        store.DECISA: t("decisa"),
    }


def category_words() -> dict[str, str]:
    """Il tipo di un contrasto, nella lingua dell'interfaccia: un'etichetta per chi decide."""
    return {
        "storica": t("contraddizione con un fatto storico"),
        "cronologia": t("date o ordine degli eventi"),
        "terminologia": t("differenza di nomi o termini"),
        "incertezza": t("le fonti stesse non concordano"),
        "fonte_debole": t("la fonte del contrasto è debole"),
        "fiction": t("contraddizione con la storia del libro"),
    }


def _link(url: str, title: str) -> str:
    """Un indirizzo apribile. Solo http(s): un file di nome `x.pdf` (un documento dell'autore)
    resta testo."""
    if url.startswith("http"):
        return f'<a href="{escape(url, quote=True)}">{escape(title or url)}</a>'
    return escape(title or url)


class ResultRow(QFrame):
    """Un risultato (o una pagina letta): titolo apribile, estratto leggibile, e «Includi»."""

    include_requested = Signal(str)
    discard_requested = Signal(str, bool)  # (indirizzo, scartare?)

    def __init__(self, hit: store.Hit, page: store.PageRead | None, *, web_on: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.url = hit.url
        self.setObjectName("resultRow")
        self.setStyleSheet("QFrame#resultRow { border-left: 3px solid palette(mid); }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 4, 4)
        testa = QHBoxLayout()
        self.title = QLabel(link_style() + _link(hit.url, hit.title))
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setOpenExternalLinks(True)
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        testa.addWidget(self.title, 1)
        stato = page.status if page is not None else ""
        parola = (t("scartato") if hit.discarded or (page is not None and page.discarded) else
                  t("escluso") if hit.status == "escluso" else
                  t("non leggibile") if stato == "non_leggibile" else
                  t("non pertinente") if stato == "non_pertinente" else
                  t("letta") if page is not None else t("non letta"))
        self.state = QLabel(parola)
        self.state.setStyleSheet(f"color: {muted_color(self)};")
        if page is not None and page.note:
            self.state.setToolTip(page.note)
        testa.addWidget(self.state)
        self.read_button: QPushButton | None = None
        self.include: QPushButton | None = None
        if page is not None and stato == "":
            self.read_button = QPushButton(t("Leggi l'estratto"))
            self.read_button.setCheckable(True)
            self.read_button.setMinimumHeight(44)
            self.read_button.setAccessibleName(t("Leggi l'estratto") + ": " + (hit.title or hit.url))
            testa.addWidget(self.read_button)
        elif stato != "non_leggibile":
            self.include = QPushButton(t("Includi nella ricerca"))
            self.include.setMinimumHeight(44)
            self.include.setAccessibleName(t("Includi nella ricerca") + ": " + (hit.title or hit.url))
            if web_on:
                self.include.setToolTip(t("Legge la pagina e la aggiunge alle fonti di questo "
                                          "punto."))
            else:
                self.include.setEnabled(False)
                self.include.setToolTip(t("La ricerca sul web è spenta: accendila in "
                                          "Impostazioni per leggere le pagine."))
            self.include.clicked.connect(lambda: self.include_requested.emit(self.url))
            testa.addWidget(self.include)
        scartato = hit.discarded or (page is not None and page.discarded)
        self.discard = QPushButton(t("Ripristina") if scartato else t("Scarta"))
        self.discard.setMinimumHeight(44)
        self.discard.setToolTip(t("Lo scarto toglie questo risultato dal materiale che legge la "
                                  "stesura; non cancella niente e si può annullare."))
        self.discard.setAccessibleName(self.discard.text() + ": " + (hit.title or hit.url))
        self.discard.clicked.connect(
            lambda: self.discard_requested.emit(self.url, not scartato))
        testa.addWidget(self.discard)
        lay.addLayout(testa)
        if hit.status == "escluso":
            escluso = QLabel(t("Dominio escluso nelle impostazioni: la pagina non è stata "
                               "scaricata."))
            escluso.setWordWrap(True)
            escluso.setStyleSheet(f"color: {muted_color(self)};")
            lay.addWidget(escluso)
        if page is not None and stato and page.note:
            motivo = QLabel(page.note)  # perché la pagina non è andata al modello
            motivo.setWordWrap(True)
            motivo.setStyleSheet(f"color: {muted_color(self)};")
            lay.addWidget(motivo)
        if hit.snippet:
            snippet = QLabel(hit.snippet)
            snippet.setWordWrap(True)
            snippet.setStyleSheet(f"color: {muted_color(self)};")
            lay.addWidget(snippet)
        self.excerpt: QPlainTextEdit | None = None
        if page is not None and stato == "":
            self.excerpt = QPlainTextEdit(page.text[:4000])
            self.excerpt.setReadOnly(True)
            self.excerpt.setMaximumHeight(180)
            self.excerpt.setVisible(False)
            self.excerpt.setAccessibleName(t("Estratto della pagina"))
            lay.addWidget(self.excerpt)
            self.read_button.toggled.connect(self.excerpt.setVisible)


class PointBox(QGroupBox):
    """Un punto del capitolo, con quello che la ricerca ne ha tratto."""

    include_requested = Signal(int, str)  # (punto, indirizzo)
    extend_requested = Signal(int)
    discard_requested = Signal(str, str, bool)  # (tipo, id, scartare?)

    def __init__(self, punto: store.PointData, domande: list[store.Question], *, web_on: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(f"{punto.index + 1}. {punto.text}", parent)
        self.index = punto.index
        self.rows: list[ResultRow] = []
        lay = QVBoxLayout(self)

        lay.addWidget(self._heading(t("Materiale trovato")))
        if punto.findings:
            for j, f in enumerate(punto.findings):
                livello = (f.level or "").strip().upper()[:1]
                uso = (f"<br>{escape(t('Come può servire:'))} {escape(f.use)}" if f.use else "")
                tipo = (escape(t("Tipo di fonte: {0}").format(livello)) + " · " if livello else "")
                voce = QLabel(
                    link_style() + f"<b>{escape(f.claim)}</b><br>{escape(f.detail)}{uso}<br>"
                    + tipo + escape(t("Fonte:")) + " " + _link(f.url, f.source))
                voce.setTextFormat(Qt.TextFormat.RichText)
                voce.setOpenExternalLinks(True)
                voce.setWordWrap(True)
                voce.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                riga_m = QHBoxLayout()
                riga_m.addWidget(voce, 1)
                scarta = QPushButton(t("Ripristina") if f.excluded else t("Scarta"))
                scarta.setMinimumHeight(44)
                scarta.setAccessibleName(scarta.text() + ": " + f.claim)
                scarta.setToolTip(t("Lo scarto toglie questo materiale da quello che legge la "
                                    "stesura; non cancella niente e si può annullare."))
                scarta.clicked.connect(
                    lambda _=False, k=f"{punto.index}:{j}", e=f.excluded:
                    self.discard_requested.emit("finding", k, not e))
                riga_m.addWidget(scarta)
                lay.addLayout(riga_m)
                if f.excluded:
                    voce.setStyleSheet(f"color: {muted_color(self)};")
                    voce.setText(escape(t("scartato")) + " — " + voce.text())
        else:
            vuoto = QLabel(t("Nessun materiale trovato per questo punto: nel libro resta "
                             "com'è."))
            vuoto.setStyleSheet(f"color: {muted_color(self)};")
            lay.addWidget(vuoto)

        mie = [q for q in domande if q.point == punto.index]
        if mie:
            lay.addWidget(self._heading(t("Contrasti segnalati")))
            parole = status_words()
            for q in mie:
                stato_q = t("scartata") if q.discarded else parole.get(q.status, q.status)
                tipo = category_words().get(q.category, "") if q.category else ""
                riga = QLabel(f"• {q.text} — {stato_q}" + (f" ({tipo})" if tipo else ""))
                riga.setWordWrap(True)
                riga_q = QHBoxLayout()
                riga_q.addWidget(riga, 1)
                scarta_q = QPushButton(t("Ripristina") if q.discarded else t("Scarta"))
                scarta_q.setMinimumHeight(44)
                scarta_q.setAccessibleName(scarta_q.text() + ": " + q.text)
                scarta_q.setToolTip(t("Una questione scartata non conta più per la stesura né per "
                                      "la revisione; non si cancella e si può ripristinare."))
                scarta_q.clicked.connect(
                    lambda _=False, i=q.id, d=q.discarded:
                    self.discard_requested.emit("question", i, not d))
                riga_q.addWidget(scarta_q)
                lay.addLayout(riga_q)

        lay.addWidget(self._heading(t("Risultati trovati e pagine lette")))
        lette = {p.url: p for p in punto.pages}
        visti: set[str] = set()
        for h in punto.results:
            visti.add(h.url)
            self._add_row(lay, h, lette.get(h.url), web_on)
        for p in punto.pages:  # pagine lette che non sono fra i risultati (quelle dell'autore)
            if p.url not in visti:
                self._add_row(lay, store.Hit(p.title, p.url, ""), p, web_on)
        if not punto.results and not punto.pages:
            vuoto = QLabel(t("Niente trovato in rete per questo punto."))
            vuoto.setStyleSheet(f"color: {muted_color(self)};")
            lay.addWidget(vuoto)

        self.extend = QPushButton(t("Estendi la ricerca"))
        self.extend.setMinimumHeight(44)
        self.extend.setAccessibleName(t("Estendi la ricerca") + ": " + punto.text)
        if web_on:
            self.extend.setToolTip(t("Nuove interrogazioni e più risultati per questo punto."))
        else:
            self.extend.setEnabled(False)
            self.extend.setToolTip(t("La ricerca sul web è spenta: accendila in Impostazioni "
                                     "per estendere la ricerca."))
        self.extend.clicked.connect(lambda: self.extend_requested.emit(self.index))
        lay.addWidget(self.extend, 0, Qt.AlignmentFlag.AlignLeft)

    def _heading(self, testo: str) -> QLabel:
        etichetta = QLabel(testo)
        etichetta.setStyleSheet("font-weight: bold; margin-top: 6px;")
        return etichetta

    def _add_row(self, lay: QVBoxLayout, hit: store.Hit, page, web_on: bool) -> None:
        riga = ResultRow(hit, page, web_on=web_on)
        riga.include_requested.connect(lambda url: self.include_requested.emit(self.index, url))
        riga.discard_requested.connect(
            lambda url, on: self.discard_requested.emit("url", url, on))
        self.rows.append(riga)
        lay.addWidget(riga)


class ChapterPage(QScrollArea):
    """La scheda di un capitolo: l'essenziale e i suoi punti."""

    include_requested = Signal(int, int, str)  # (capitolo, punto, indirizzo)
    extend_requested = Signal(int, int)
    discard_requested = Signal(int, str, str, bool)  # (capitolo, tipo, id, scartare?)

    def __init__(self, number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.number = number
        self.built = False
        self.boxes: list[PointBox] = []
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def fill(self, chapter: dict, data: store.ChapterData | None, *, web_on: bool) -> None:
        corpo = QWidget()
        lay = QVBoxLayout(corpo)
        self.boxes = []
        titolo = QLabel(f"<b>{escape(str(chapter.get('title') or ''))}</b>")
        titolo.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(titolo)
        fa = str(chapter.get("does") or "").strip()
        if fa:
            descr = QLabel(fa)
            descr.setWordWrap(True)
            lay.addWidget(descr)
        if data is None:
            vuoto = QLabel(t("La ricerca di questo capitolo non è ancora stata fatta."))
            vuoto.setStyleSheet(f"color: {muted_color(self)};")
            lay.addWidget(vuoto)
        else:
            barra = QHBoxLayout()
            tutto = QPushButton(t("Scarta tutto il materiale del capitolo"))
            tutto.setMinimumHeight(44)
            tutto.setToolTip(t("Toglie dal materiale che legge la stesura tutto ciò che la "
                               "ricerca ha trovato per questo capitolo; non cancella niente."))
            tutto.clicked.connect(lambda: self.discard_requested.emit(self.number, "all", "", True))
            barra.addWidget(tutto)
            ripristina = QPushButton(t("Ripristina tutto"))
            ripristina.setMinimumHeight(44)
            ripristina.clicked.connect(
                lambda: self.discard_requested.emit(self.number, "all", "", False))
            barra.addWidget(ripristina)
            barra.addStretch(1)
            lay.addLayout(barra)
            for punto in data.points:
                box = PointBox(punto, data.questions, web_on=web_on)
                box.discard_requested.connect(
                    lambda tipo, ident, on, n=self.number:
                    self.discard_requested.emit(n, tipo, ident, on))
                box.include_requested.connect(
                    lambda p, url, n=self.number: self.include_requested.emit(n, p, url))
                box.extend_requested.connect(
                    lambda p, n=self.number: self.extend_requested.emit(n, p))
                self.boxes.append(box)
                lay.addWidget(box)
            if data.warnings:
                avvisi = QLabel(t("Avvertenze:") + "\n" + "\n".join(f"• {w}" for w in data.warnings))
                avvisi.setWordWrap(True)
                avvisi.setStyleSheet(f"color: {muted_color(self)};")
                lay.addWidget(avvisi)
        lay.addStretch(1)
        self.setWidget(corpo)
        self.built = True


class ResearchExplorer(QWidget):
    """Le tre schede della fase Ricerca."""

    include_requested = Signal(int, int, str)
    extend_requested = Signal(int, int)
    discard_requested = Signal(int, str, str, bool)
    discard_old_requested = Signal()
    resume_requested = Signal()
    import_blocked_requested = Signal()
    fetch_blocked_requested = Signal()
    open_blocked_folder_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.landscape_view = QTextBrowser()
        self.landscape_view.setOpenExternalLinks(True)
        self.landscape_view.setAccessibleName(t("Cosa esiste già"))
        self.tabs.addTab(self.landscape_view, t("Cosa esiste già"))

        self.chapters_tabs = QTabWidget()
        self.chapters_tabs.setUsesScrollButtons(True)
        self.chapters_tabs.currentChanged.connect(self._ensure_built)
        self.tabs.addTab(self.chapters_tabs, t("Fonti dei capitoli"))

        self.questions = ChatPanel(
            side_title=t("Questioni aperte"), attach=True,
            side_placeholder=t("*Qui compaiono le questioni che la ricerca non ha saputo "
                               "documentare, con il loro stato.*"))
        # Sopra la conversazione, i comandi che riguardano tutte le questioni.
        self.questions_page = QWidget()
        colonna = QVBoxLayout(self.questions_page)
        colonna.setContentsMargins(0, 0, 0, 0)
        barra = QHBoxLayout()
        self.discard_old = QPushButton(t("Scarta tutte le questioni vecchie"))
        self.discard_old.setMinimumHeight(44)
        self.discard_old.setToolTip(t("Scarta le questioni nate prima del riorientamento della "
                                      "ricerca: dicevano «non documentato» anche per ciò che "
                                      "inventi tu. Non si cancella niente e si ripristinano una "
                                      "per una."))
        self.discard_old.clicked.connect(self.discard_old_requested)
        barra.addWidget(self.discard_old)
        self.resume = QPushButton(t("Riprendi l'approfondimento"))
        self.resume.setMinimumHeight(44)
        self.resume.setToolTip(t("Riprende le questioni rimaste ferme dopo un'interruzione della "
                                 "ricerca."))
        self.resume.clicked.connect(self.resume_requested)
        barra.addWidget(self.resume)
        barra.addStretch(1)
        colonna.addLayout(barra)
        colonna.addWidget(self.questions, 1)
        self.tabs.addTab(self.questions_page, t("Questioni aperte"))

        # Scheda Pagine Bloccate
        self.blocked_page = QWidget()
        b_col = QVBoxLayout(self.blocked_page)
        b_col.setContentsMargins(12, 12, 12, 12)
        b_bar = QHBoxLayout()
        self.btn_import_blocked = QPushButton(t("Importa le pagine ricevute"))
        self.btn_import_blocked.setMinimumHeight(44)
        self.btn_import_blocked.setToolTip(t("Importa i file presenti in research/bloccate/ricevute/ e aggiorna le fonti."))
        self.btn_import_blocked.clicked.connect(self.import_blocked_requested)
        b_bar.addWidget(self.btn_import_blocked)
        self.btn_fetch_blocked = QPushButton(t("Recupera le pagine in coda"))
        self.btn_fetch_blocked.setMinimumHeight(44)
        self.btn_fetch_blocked.setToolTip(t(
            "Chiede le pagine in coda al servizio esterno indicato in Impostazioni e le importa "
            "come se le avessi ricevute. Non risolve niente da sé."))
        self.btn_fetch_blocked.clicked.connect(self.fetch_blocked_requested)
        b_bar.addWidget(self.btn_fetch_blocked)
        self.btn_open_blocked_dir = QPushButton(t("Apri la cartella della coda"))
        self.btn_open_blocked_dir.setMinimumHeight(44)
        self.btn_open_blocked_dir.setToolTip(t("Apre la cartella research/bloccate/ nel file manager del sistema."))
        self.btn_open_blocked_dir.clicked.connect(self.open_blocked_folder_requested)
        b_bar.addWidget(self.btn_open_blocked_dir)
        b_bar.addStretch(1)
        b_col.addLayout(b_bar)

        self.blocked_summary = QLabel(t("Nessuna pagina bloccata registrata."))
        self.blocked_summary.setWordWrap(True)
        b_col.addWidget(self.blocked_summary)

        self.blocked_browser = QTextBrowser()
        self.blocked_browser.setOpenExternalLinks(True)
        b_col.addWidget(self.blocked_browser, 1)
        self.tabs.addTab(self.blocked_page, t("Pagine bloccate"))

        lay.addWidget(self.tabs)

        self._outline: list[dict] = []
        self._data: dict[int, store.ChapterData] = {}
        self._web_on = False
        self._pages: dict[int, ChapterPage] = {}

    # ---- dati -------------------------------------------------------------------
    def set_data(self, landscape: store.LandscapeData | None, outline: list[dict],
                 chapters: list[store.ChapterData], *, web_on: bool) -> None:
        """Ridisegna l'esploratore. Le schede dei capitoli si costruiscono quando si aprono."""
        self._web_on = web_on
        self._data = {c.number: c for c in chapters}
        self._set_landscape(landscape)
        corrente = self.chapters_tabs.currentIndex()
        if [int(c.get("number") or 0) for c in outline] != list(self._pages):
            self.chapters_tabs.blockSignals(True)
            self.chapters_tabs.clear()
            self._pages = {}
            for c in outline:
                numero = int(c.get("number") or 0)
                pagina = ChapterPage(numero)
                pagina.include_requested.connect(self.include_requested)
                pagina.extend_requested.connect(self.extend_requested)
                pagina.discard_requested.connect(self.discard_requested)
                self._pages[numero] = pagina
                titolo = str(c.get("title") or "")
                breve = titolo if len(titolo) <= 22 else titolo[:21].rstrip() + "…"
                self.chapters_tabs.addTab(pagina, f"{numero} · {breve}" if titolo else str(numero))
                self.chapters_tabs.setTabToolTip(self.chapters_tabs.count() - 1,
                                                 f"{numero} · {titolo}" if titolo else str(numero))
            self.chapters_tabs.blockSignals(False)
        self._outline = outline
        for pagina in self._pages.values():
            pagina.built = False
        self.chapters_tabs.setCurrentIndex(max(0, min(corrente, self.chapters_tabs.count() - 1)))
        self._ensure_built(self.chapters_tabs.currentIndex())

    def set_questions_count(self, quante: int) -> None:
        titolo = t("Questioni aperte")
        self.tabs.setTabText(2, f"{titolo} ({quante})" if quante else titolo)

    def show_questions(self) -> None:
        self.tabs.setCurrentWidget(self.questions_page)

    def page(self, number: int) -> ChapterPage | None:
        """La scheda di un capitolo, costruita (per i test e per chi vuole guardarci dentro)."""
        pagina = self._pages.get(number)
        if pagina is not None and not pagina.built:
            self._build(pagina)
        return pagina

    def _ensure_built(self, indice: int) -> None:
        pagina = self.chapters_tabs.widget(indice)
        if isinstance(pagina, ChapterPage) and not pagina.built:
            self._build(pagina)

    def _build(self, pagina: ChapterPage) -> None:
        capitolo = next((c for c in self._outline
                         if int(c.get("number") or 0) == pagina.number), {})
        pagina.fill(capitolo, self._data.get(pagina.number), web_on=self._web_on)

    def _set_landscape(self, dati: store.LandscapeData | None) -> None:
        if dati is None:
            self.landscape_view.setMarkdown(t("*Il panorama non è ancora stato fatto.*"))
            return
        testo = store.render_landscape_md("", dati)
        testo += "\n\n---\n\n## " + t("Cosa è stato cercato") + "\n\n"
        testo += "\n".join(f"- {q}" for q in dati.queries) + "\n"
        if dati.pages:
            testo += "\n## " + t("Pagine lette") + "\n\n"
            for p in dati.pages:
                estratto = " ".join(p.text[:300].split())
                testo += f"- [{p.title or p.url}]({p.url})\n  > {estratto}\n"
        self.landscape_view.setMarkdown(testo)
        recolor_links(self.landscape_view.document())

    def set_blocked_data(self, items: list[Any]) -> None:
        """Aggiorna la vista delle pagine bloccate e il conteggio sulla scheda."""
        titolo = t("Pagine bloccate")
        in_coda = [it for it in items if getattr(it, "status", "") == "in_coda"]
        self.tabs.setTabText(3, f"{titolo} ({len(in_coda)})" if in_coda else titolo)

        if not items:
            self.blocked_summary.setText(t("Nessuna pagina bloccata registrata."))
            msg = t("Quando un sito rifiuta la lettura (CAPTCHA, Cloudflare, HTTP 403), la pagina compare qui e passa al browser esterno o alla coda.")
            self.blocked_browser.setHtml(f"<p style='color: {muted_color(self)};'>{msg}</p>")
            return

        conteggi: dict[str, int] = {}
        for it in items:
            conteggi[it.reason] = conteggi.get(it.reason, 0) + 1

        riepilogo = ", ".join(f"{cnt} {rsn}" for rsn, cnt in conteggi.items())
        self.blocked_summary.setText(
            f"{len(items)} {t('pagine bloccate')} ({riepilogo}). {len(in_coda)} {t('in attesa')}."
        )

        righe = []
        for it in items:
            righe.append(
                f"<tr style='border-bottom: 1px solid #333;'>"
                f"<td style='padding: 6px; font-family: monospace;'>{escape(it.id)}</td>"
                f"<td style='padding: 6px;'>Cap. {it.chapter}, p. {it.point}</td>"
                f"<td style='padding: 6px; font-weight: bold;'>{escape(it.reason)}</td>"
                f"<td style='padding: 6px;'>{escape(it.status)}</td>"
                f"<td style='padding: 6px;'><a href='{escape(it.url, quote=True)}'>{escape(it.url[:60])}</a></td>"
                f"</tr>"
            )
        html = (
            f"<table style='width: 100%; border-collapse: collapse; text-align: left; font-size: 13px;'>"
            f"<thead><tr style='border-bottom: 2px solid #555;'>"
            f"<th style='padding: 6px;'>ID</th>"
            f"<th style='padding: 6px;'>{t('Posizione')}</th>"
            f"<th style='padding: 6px;'>{t('Motivo')}</th>"
            f"<th style='padding: 6px;'>{t('Stato')}</th>"
            f"<th style='padding: 6px;'>{t('Indirizzo')}</th>"
            f"</tr></thead>"
            f"<tbody>{''.join(righe)}</tbody>"
            f"</table>"
        )
        self.blocked_browser.setHtml(html)
