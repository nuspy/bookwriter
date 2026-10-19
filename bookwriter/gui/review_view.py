"""La pagina della fase «Revisione»: i punti a sinistra, la conversazione al centro, le prove a destra.

L'app mette davanti all'autore, un punto alla volta, quello che la ricerca ha trovato e quello che
non le torna; ne parlano in più scambi; la decisione si scrive **solo** quando l'autore preme
«Conferma» sulla scheda «Registro così…» (o scrive lui la formulazione con «Decido io…»). Tutto è
saltabile e niente è obbligatorio: la revisione non blocca mai la stesura.

Il widget mostra dati e lancia richieste (segnali): a chiamare il modello e a scrivere su disco
pensa la finestra, con i suoi lavori di sfondo (vedi ``MainWindow._review_*``).

Ogni comando si raggiunge con la tastiera; i bersagli da toccare sono alti almeno 44 px; lo stato
di un punto è sempre scritto in parole (mai il solo colore).
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from bookwriter.forge import review as rv
from bookwriter.gui.chat import ChatPanel, muted_color
from bookwriter.gui.i18n import t


def status_words() -> dict[str, str]:
    """Gli stati di un punto, nella lingua dell'interfaccia."""
    return {
        rv.APERTO: t("da discutere"),
        rv.IN_DISCUSSIONE: t("in discussione"),
        rv.CONCORDATO: t("concordato"),
        rv.IMPOSTO: t("deciso da te"),
        rv.FONTE: t("con fonte"),
        rv.TRALASCIATO: t("tralasciato"),
        rv.RIMANDATO: t("rimandato"),
        rv.OBSOLETO: t("superato"),
    }


def kind_words() -> dict[str, str]:
    return {
        rv.MATERIALE: t("materiale trovato"),
        rv.IDEA: t("idea per espandere"),
        rv.TENSIONE: t("contrasto con un fatto"),
        rv.INCOERENZA: t("incoerenza"),
        rv.DEBOLEZZA: t("debolezza"),
        rv.NON_DOCUMENTATO: t("contrasto segnalato"),
        rv.SCELTA_CAMPO: t("scelta sul campo"),
        rv.LIBERO: t("tuo"),
    }


def decision_words() -> dict[str, str]:
    """I modi di chiudere un punto, come li legge l'autore."""
    return {
        rv.D_CONCORDATA: t("Concordata con l'assistente"),
        rv.D_IMPOSTA: t("Tengo la mia posizione"),
        rv.D_FONTE: t("Con questa fonte"),
        rv.D_TRALASCIA: t("Tralascia"),
    }


def evidence_markdown(point: rv.ReviewPoint | None) -> str:
    """Le prove di un punto, letterali, con la fonte e il livello: la colonna di destra."""
    if point is None:
        return ""
    righe = [f"## {point.title}", "", point.statement, ""]
    if point.evidence:
        righe.append("### " + t("Prove"))
        for e in point.evidence:
            livello = " · " + t("livello {0}").format(e.level) if e.level else ""
            fonte = f" — [{e.source or e.url}]({e.url})" if e.url else (
                f" — {e.source}" if e.source else "")
            righe.append(f"- {e.quote}{livello}{fonte}")
        righe.append("")
    if point.stakes:
        righe += ["### " + t("Cosa è in gioco"), point.stakes, ""]
    if point.decision is not None:
        d = point.decision
        righe += ["### " + t("Decisione"), f"**{decision_words().get(d.kind, d.kind)}**", "",
                  d.text or t("Il libro non ne parla."), ""]
        if d.structure_note:
            righe += [f"*{d.structure_note}*", ""]
    return "\n".join(righe)


class DecisionCard(QFrame):
    """«Registro così…»: la formulazione che entrerebbe nel libro, da confermare o correggere."""

    confirmed = Signal(str, str, bool)  # (esito, formulazione, note negli appunti?)
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("decisionCard")
        self.setStyleSheet("QFrame#decisionCard { border: 1px solid palette(mid); "
                           "border-radius: 6px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        self.title = QLabel(t("Registro così:"))
        self.title.setStyleSheet("font-weight: bold;")
        lay.addWidget(self.title)
        riga = QHBoxLayout()
        self.kind = QComboBox()
        for chiave, parola in decision_words().items():
            if chiave != rv.D_FONTE:
                self.kind.addItem(parola, chiave)
        self.kind.setMinimumHeight(44)
        self.kind.setAccessibleName(t("Come si chiude il punto"))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        riga.addWidget(self.kind, 1)
        lay.addLayout(riga)
        self.text = QPlainTextEdit()
        self.text.setMinimumHeight(88)
        self.text.setMaximumHeight(140)
        self.text.setAccessibleName(t("Formulazione da usare nel libro"))
        self.text.setPlaceholderText(t("La formulazione esatta, nella tua voce, come entrerà nel "
                                       "libro."))
        lay.addWidget(self.text)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {muted_color(self)};")
        lay.addWidget(self.hint)
        self.keep_note = QCheckBox(t("Metti le note negli appunti (prove contrarie, "
                                     "formulazioni scartate)"))
        self.keep_note.setChecked(False)  # le note negli appunti sono una scelta, non il default
        self.keep_note.setMinimumHeight(44)
        self.keep_note.setToolTip(t("Le note vanno in un file a parte che la stesura non legge: "
                                    "il testo del libro resta nella tua voce in ogni caso."))
        lay.addWidget(self.keep_note)
        bottoni = QHBoxLayout()
        self.confirm = QPushButton(t("Conferma"))
        self.confirm.setMinimumHeight(44)
        self.confirm.clicked.connect(self._confirm)
        bottoni.addWidget(self.confirm)
        self.later = QPushButton(t("Non ancora"))
        self.later.setMinimumHeight(44)
        self.later.clicked.connect(self.dismissed)
        bottoni.addWidget(self.later)
        bottoni.addStretch(1)
        lay.addLayout(bottoni)
        self.point_kind = ""
        self._source = ""
        self._url = ""
        self.by_author = False
        self._structure = ""
        self._note = ""
        self.setVisible(False)

    def _kind_changed(self) -> None:
        tralascia = self.kind.currentData() == rv.D_TRALASCIA
        self.text.setEnabled(not tralascia)
        self.hint.setText(
            t("Resta la tua posizione: le prove contrarie non entrano nel testo.")
            if self.kind.currentData() == rv.D_IMPOSTA else "")

    def _labels_for(self, point_kind: str) -> None:
        """Il materiale e le idee si chiudono con «entra» o «lo lascio fuori», non con una
        posizione da tenere."""
        semplice = point_kind in rv.KINDS_WITHOUT_TEXT
        self.kind.blockSignals(True)
        self.kind.clear()
        if semplice:
            self.kind.addItem(t("Entra nel libro"), rv.D_CONCORDATA)
            self.kind.addItem(t("Lo lascio fuori"), rv.D_TRALASCIA)
        else:
            for chiave, parola in decision_words().items():
                if chiave != rv.D_FONTE:
                    self.kind.addItem(parola, chiave)
        self.kind.blockSignals(False)
        self.text.setPlaceholderText(
            t("Se vuoi, scrivi come usarlo (facoltativo).") if semplice else
            t("La formulazione esatta, nella tua voce, come entrerà nel libro."))

    def open(self, proposal: dict | None = None, *, by_author: bool = False,
             point_kind: str = "") -> None:
        """Mostra la scheda: con la proposta dell'app, o vuota per «Decido io…»."""
        proposal = proposal or {}
        self.point_kind = point_kind
        self._labels_for(point_kind)
        esito = proposal.get("kind") or rv.D_CONCORDATA
        if esito == rv.D_FONTE:
            esito = rv.D_CONCORDATA  # la fonte si tiene a parte; qui l'autore sceglie come chiudere
        self.kind.setCurrentIndex(max(0, self.kind.findData(esito)))
        self.text.setPlainText(proposal.get("text", ""))
        self._source = proposal.get("source", "")
        self._url = proposal.get("url", "")
        self._structure = proposal.get("structure_note", "")
        self._note = proposal.get("note", "")
        self.by_author = by_author
        self.title.setText(t("Scrivi tu la decisione:") if by_author else t("Registro così:"))
        self._kind_changed()
        if self._structure:
            self.hint.setText(self.hint.text() + ("\n" if self.hint.text() else "")
                              + t("Attenzione, tocca la struttura: {0}").format(self._structure))
        self.setVisible(True)
        self.text.setFocus()

    def values(self) -> dict:
        return {"kind": self.kind.currentData(), "text": self.text.toPlainText().strip(),
                "keep_note": self.keep_note.isChecked(), "note": self._note,
                "source": self._source, "url": self._url, "by_author": self.by_author,
                "structure_note": self._structure}

    def _confirm(self) -> None:
        v = self.values()
        facoltativo = self.point_kind in rv.KINDS_WITHOUT_TEXT
        if v["kind"] != rv.D_TRALASCIA and not v["text"] and not facoltativo:
            self.text.setFocus()
            return
        self.setVisible(False)
        self.confirmed.emit(v["kind"], v["text"], v["keep_note"])


class ReviewPage(QWidget):
    """L'elenco dei punti, la conversazione del punto corrente e le sue prove."""

    point_selected = Signal(str)
    skip_point_requested = Signal()
    free_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(self.splitter)

        sinistra = QWidget()
        col = QVBoxLayout(sinistra)
        col.setContentsMargins(0, 0, 8, 0)
        intestazione = QLabel(t("Punti da discutere"))
        intestazione.setStyleSheet("font-weight: bold;")
        col.addWidget(intestazione)
        self.list = QListWidget()
        self.list.setAccessibleName(t("Punti da discutere"))
        self.list.setWordWrap(True)
        self.list.setSpacing(2)
        self.list.currentItemChanged.connect(self._item_changed)
        col.addWidget(self.list, 1)
        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet(f"color: {muted_color(self)};")
        col.addWidget(self.warnings)
        self.splitter.addWidget(sinistra)

        destra = QWidget()
        col2 = QVBoxLayout(destra)
        col2.setContentsMargins(0, 0, 0, 0)
        self.chat = ChatPanel(side_title=t("Prove"), attach=True,
                              side_placeholder=t("*Qui compaiono le prove del punto: cosa dice il "
                                                 "libro e cosa dicono i dati della ricerca.*"))
        col2.addWidget(self.chat, 1)
        self.card = DecisionCard()
        col2.addWidget(self.card)
        azioni = QHBoxLayout()
        self.decide_button = QPushButton(t("Decido io…"))
        self.decide_button.setToolTip(t("Scrivi tu la formulazione e come chiudere il punto, "
                                        "senza passare dall'assistente."))
        self.decide_button.clicked.connect(lambda: self.card.open(
            by_author=True, point_kind=self.current_kind))
        self.skip_button = QPushButton(t("Salta il punto"))
        self.skip_button.setToolTip(t("Resta da decidere, senza nessun effetto sul libro."))
        self.skip_button.clicked.connect(self.skip_point_requested)
        self.search_button = QPushButton(t("Cerca…"))
        self.search_button.setToolTip(t("Chiedi all'assistente di cercare riferimenti, "
                                        "verificare qualcosa o scoprire come funziona."))
        self.search_button.clicked.connect(self._prefill_search)
        self.free_button = QPushButton(t("Chat libera"))
        self.free_button.setToolTip(t("Parla di quello che vuoi: l'assistente conosce le "
                                      "decisioni prese e le sue perplessità."))
        self.free_button.clicked.connect(self.free_requested)
        for b in (self.decide_button, self.skip_button, self.search_button, self.free_button):
            b.setMinimumHeight(44)
            azioni.addWidget(b)
        azioni.addStretch(1)
        col2.addLayout(azioni)
        self.splitter.addWidget(destra)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)
        self.current_id = ""
        self.current_kind = ""
        self.free_mode = False
        self._filling = False
        self._set_enabled_actions(False)

    # ---- elenco -------------------------------------------------------------------
    def set_data(self, data: rv.ReviewData | None) -> None:
        """Ridisegna l'elenco, rimettendo il punto che era aperto."""
        self._filling = True
        try:
            self.list.clear()
            if data is None or not data.points:
                self.warnings.setText("")
                self.chat.set_notebook("")
                self._set_enabled_actions(False)
                return
            stati, tipi = status_words(), kind_words()
            for p in data.points:
                voce = QListWidgetItem(f"{stati.get(p.status, p.status)} · {tipi.get(p.kind, p.kind)}"
                                       f"\n{p.title}")
                voce.setData(Qt.ItemDataRole.UserRole, p.id)
                voce.setToolTip(p.title)
                self.list.addItem(voce)
                if p.id == self.current_id and not self.free_mode:
                    self.list.setCurrentItem(voce)
            self.warnings.setText("\n".join(data.warnings[:3]))
            self._set_enabled_actions(True)
        finally:
            self._filling = False

    def _item_changed(self, voce, _prima) -> None:
        if self._filling or voce is None:
            return
        self.free_mode = False
        self.point_selected.emit(str(voce.data(Qt.ItemDataRole.UserRole)))

    def select(self, point_id: str) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == point_id:
                self.list.setCurrentRow(i)
                return

    def next_open_id(self, data: rv.ReviewData, after: str = "") -> str:
        """Il prossimo punto ancora da decidere dopo ``after`` (o il primo), o vuoto."""
        ids = [p.id for p in data.points]
        inizio = ids.index(after) + 1 if after in ids else 0
        for p in data.points[inizio:] + data.points[:inizio]:
            if p.status in (rv.APERTO, rv.IN_DISCUSSIONE) and p.id != after:
                return p.id
        return ""

    # ---- il punto aperto -------------------------------------------------------------
    def show_point(self, point: rv.ReviewPoint) -> None:
        self.current_id, self.free_mode = point.id, False
        self.current_kind = point.kind
        self.card.setVisible(False)
        vista = self.chat.view
        vista.clear()
        for b in (point.thread or [{"role": "assistant", "text": rv.opening(point)}]):
            if b.get("role") == "notice":
                vista.add_notice(b.get("text", ""))
            else:
                vista.add_message("user" if b.get("role") == "user" else "assistant",
                                  b.get("text", ""))
        self.chat.set_notebook(evidence_markdown(point))
        vista.scroll_to_end()
        if point.proposal:
            self.card.open(point.proposal, point_kind=point.kind)
        self._set_enabled_actions(True)

    def show_free(self, data: rv.ReviewData | None, perplexities: list[str]) -> None:
        """La chat libera: la sua conversazione, e a destra le perplessità dell'app."""
        self.free_mode, self.current_id = True, ""
        self.current_kind = ""
        self.card.setVisible(False)
        vista = self.chat.view
        vista.clear()
        fili = data.free_thread if data else []
        if not fili:
            vista.add_message("assistant", t(
                "Qui puoi parlare di quello che vuoi: dubbi sul libro, cose che vorresti "
                "verificare, punti rimasti in sospeso. A destra c'è quello che non mi torna prima "
                "di scrivere. Quando hai finito, passa alla stesura."))
        for b in fili:
            if b.get("role") == "notice":
                vista.add_notice(b.get("text", ""))
            else:
                vista.add_message("user" if b.get("role") == "user" else "assistant",
                                  b.get("text", ""))
        testo = "## " + t("Le mie perplessità") + "\n\n" + (
            "\n".join(f"- {c}" for c in perplexities) if perplexities
            else t("Nessuna: puoi passare alla stesura."))
        self.chat.set_notebook(testo)
        vista.scroll_to_end()
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentRow(-1)
        self.list.blockSignals(False)
        if data and data.free_proposal:
            self.card.open(data.free_proposal)
        self._set_enabled_actions(True)

    def _prefill_search(self) -> None:
        composer = self.chat.composer
        if not composer.input.toPlainText().strip():
            composer.input.setPlainText(t("Cerca riferimenti su "))
        composer.input.setFocus()

    def _set_enabled_actions(self, on: bool) -> None:
        for b in (self.decide_button, self.search_button, self.free_button):
            b.setEnabled(on)
        self.skip_button.setEnabled(on and not self.free_mode and bool(self.current_id))
