"""La conversazione sul libro: una chat, non un modulo.

Le battute dell'autore stanno a destra, in un fumetto; quelle dell'assistente a sinistra, come
testo corrente, in markdown. La risposta compare mentre il modello la scrive, e finché non
comincia un'indicazione dice da quanto sta pensando: un modello che ragiona può metterci un
minuto, e un minuto di schermo fermo sembra un guasto.

Sotto, il campo in cui si scrive: Invio manda, Maiuscolo+Invio va a capo, e si allunga con il
testo fino a otto righe. Accanto, il quaderno del libro, che cresce con la conversazione.

Tutto è fatto con i colori della tavolozza, così regge il tema chiaro e quello scuro; il testo
secondario è un impasto di testo e fondo calcolato apposta perché resti leggibile (non il «mid»
della tavolozza, che su certi temi non arriva a un contrasto accettabile).
"""

from __future__ import annotations

import math
import re
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPalette, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from bookwriter.gui.i18n import t

#: La colonna della conversazione non si allarga oltre questo: righe più lunghe non si leggono.
COLUMN_MAX = 860
#: Il fumetto dell'autore non supera questa frazione della colonna.
USER_SHARE = 0.8
#: Quante righe può crescere il campo di scrittura prima di scorrere.
INPUT_MAX_LINES = 8
#: Ogni quanto si ridisegna la risposta che arriva: ridisegnarla a ogni pezzo costa troppo.
REDRAW_MS = 60
#: Quanto più grande del carattere di sistema è il testo della conversazione: si legge a lungo,
#: e il carattere di serie delle finestre (9 punti) è pensato per etichette e pulsanti.
READING_SCALE = 1.3


def _mix(a: QColor, b: QColor, share: float) -> str:
    """``share`` di ``a`` e il resto di ``b``, come colore CSS."""
    r = round(a.red() * share + b.red() * (1 - share))
    g = round(a.green() * share + b.green() * (1 - share))
    bl = round(a.blue() * share + b.blue() * (1 - share))
    return f"rgb({r},{g},{bl})"


def link_color() -> str:
    """Il colore dei collegamenti, leggibile sul fondo (chiaro o scuro).

    Quello di serie della tavolozza è un blu scuro: su un tema scuro il testo del collegamento
    sparisce nel fondo.
    """
    from PySide6.QtWidgets import QApplication

    scuro = QApplication.palette().color(QPalette.ColorRole.Base).lightness() < 128
    return "#7ab8f5" if scuro else "#0b57d0"


def link_style() -> str:
    """Da mettere in testa a un testo ricco (``QLabel``): colora i suoi collegamenti."""
    return f"<style>a {{ color: {link_color()}; }}</style>"


def recolor_links(document) -> None:
    """Colora i collegamenti di un documento (``QTextBrowser``), dopo che il testo è stato messo."""
    from PySide6.QtGui import QBrush, QTextCharFormat

    colore = QBrush(QColor(link_color()))
    cursore = QTextCursor(document)
    blocco = document.begin()
    while blocco.isValid():
        it = blocco.begin()
        while not it.atEnd():
            frammento = it.fragment()
            if frammento.isValid() and frammento.charFormat().isAnchor():
                c = QTextCursor(document)
                c.setPosition(frammento.position())
                c.setPosition(frammento.position() + frammento.length(),
                              QTextCursor.MoveMode.KeepAnchor)
                formato = QTextCharFormat()
                formato.setForeground(colore)
                c.mergeCharFormat(formato)
            it += 1
        blocco = blocco.next()
    del cursore


def reading_font(widget: QWidget, scale: float = READING_SCALE) -> None:
    """Ingrandisce il carattere del widget rispetto a quello di sistema.

    In proporzione, non a una misura fissa: chi ha ingrandito il testo in Windows lo ritrova
    ingrandito anche qui.
    """
    carattere = widget.font()
    if carattere.pointSizeF() > 0:
        carattere.setPointSizeF(carattere.pointSizeF() * scale)
    elif carattere.pixelSize() > 0:
        carattere.setPixelSize(round(carattere.pixelSize() * scale))
    widget.setFont(carattere)


def muted_color(widget: QWidget) -> str:
    """Il colore del testo secondario: leggibile su chiaro e su scuro."""
    pal = widget.palette()
    return _mix(pal.color(QPalette.ColorRole.Text), pal.color(QPalette.ColorRole.Base), 0.72)


# --------------------------------------------------------------------------- #
#  un blocco di testo che si adatta al contenuto
# --------------------------------------------------------------------------- #
class Prose(QTextBrowser):
    """Markdown che occupa l'altezza che gli serve, senza barre di scorrimento.

    È un ``QTextBrowser`` e non una ``QLabel`` per due ragioni: l'interlinea (che in una
    ``QLabel`` non si regola, e sotto 1,5 il testo lungo si legge male) e la selezione del
    testo con la tastiera.
    """

    def __init__(self, *, hug: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hug = hug  # stringersi attorno al testo (il fumetto dell'autore) o occupare la riga
        self._cap = COLUMN_MAX
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setOpenExternalLinks(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().setDocumentMargin(0)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        reading_font(self)

    def set_markdown(self, text: str) -> None:
        self.setMarkdown(text or "")
        recolor_links(self.document())
        cursore = QTextCursor(self.document())
        cursore.select(QTextCursor.SelectionType.Document)
        formato = QTextBlockFormat()
        formato.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        formato.setBottomMargin(8)
        cursore.mergeBlockFormat(formato)
        # Lo spazio dopo l'ultimo paragrafo non separa niente: allargherebbe solo il fumetto.
        ultimo = QTextCursor(self.document().lastBlock())
        fine = QTextBlockFormat()
        fine.setBottomMargin(0)
        ultimo.mergeBlockFormat(fine)
        self._fit()

    def set_cap(self, width: int) -> None:
        """La larghezza massima, quando il blocco si stringe attorno al testo."""
        self._cap = max(120, width)
        self._fit()

    def _fit(self) -> None:
        doc = self.document()
        if self._hug:
            doc.setTextWidth(-1)
            ideale = math.ceil(doc.idealWidth()) + 4
            self.setMaximumWidth(min(ideale, self._cap))
        doc.setTextWidth(max(1, self.viewport().width()))
        altezza = math.ceil(doc.size().height()) + 2
        if altezza != self.height():
            self.setFixedHeight(altezza)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def sizeHint(self) -> QSize:
        # Stretto attorno al testo, la larghezza giusta è quella che _fit ha calcolato: quella di
        # serie di Qt (circa 250 pixel) andrebbe a capo dopo poche parole anche su uno schermo
        # largo.
        larghezza = self.maximumWidth() if self._hug else super().sizeHint().width()
        return QSize(larghezza, self.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(60, self.height())


# --------------------------------------------------------------------------- #
#  una battuta
# --------------------------------------------------------------------------- #
class Bubble(QFrame):
    """Una battuta della conversazione. Quella dell'assistente può arrivare a pezzi."""

    def __init__(self, role: str, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.role = role
        self._text = text
        self._started = 0.0
        self._thinking_kind = ""
        self.setAccessibleName(t("Tu") if role == "user" else t("Assistente"))

        lay = QVBoxLayout(self)
        self.prose = Prose(hug=role == "user")
        self.status = QLabel("")
        self.status.setVisible(False)
        self.status.setStyleSheet(f"color: {muted_color(self)}; font-style: italic;")
        reading_font(self.status, 1.1)
        if role == "user":
            self.setObjectName("userBubble")
            # Sotto meno che sopra: l'interlinea a 1,5 lascia già il suo spazio sotto l'ultima riga.
            lay.setContentsMargins(14, 10, 14, 4)
            self.setStyleSheet(
                "QFrame#userBubble { background: palette(alternate-base); "
                "border: 1px solid palette(mid); border-radius: 14px; }"
            )
            self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        else:
            lay.setContentsMargins(2, 4, 2, 4)
        lay.addWidget(self.status)
        lay.addWidget(self.prose)
        self.prose.set_markdown(text)
        self.prose.setVisible(bool(text))

        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(REDRAW_MS)
        self._redraw.timeout.connect(lambda: self.prose.set_markdown(self._text))
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)

    @property
    def text(self) -> str:
        return self._text

    # ---- la risposta che arriva --------------------------------------------
    def wait(self, label: str = "") -> None:
        """Mostra che l'assistente sta lavorando, con i secondi che passano."""
        self._started = time.monotonic()
        self._thinking_kind = label or t("Sto pensando…")
        self.status.setVisible(True)
        self._tick()
        self._clock.start()

    def reasoning(self) -> None:
        """Il modello sta ragionando: lo si dice, senza mostrarne il pensiero."""
        if not self._text:
            self._thinking_kind = t("Sto ragionando…")

    def append(self, piece: str) -> None:
        if not self._text:
            self.status.setVisible(False)
            self._clock.stop()
            self.prose.setVisible(True)
        self._text += piece
        if not self._redraw.isActive():
            self._redraw.start()

    def reset(self) -> None:
        """La richiesta è ripartita da capo: quello arrivato finora non vale più."""
        self._text = ""
        self.prose.set_markdown("")
        self.prose.setVisible(False)
        if self._started:
            self.status.setVisible(True)
            self._clock.start()

    def finish(self, text: str | None = None, *, note: str = "") -> None:
        """La risposta è completa. ``text``, se c'è, è la versione che fa fede."""
        self._clock.stop()
        self._redraw.stop()
        if text is not None:
            self._text = text
        self.prose.set_markdown(self._text)
        self.prose.setVisible(bool(self._text))
        self.status.setText(note)
        self.status.setVisible(bool(note))

    def _tick(self) -> None:
        secondi = int(time.monotonic() - self._started)
        self.status.setText(t("{0} {1} s").format(self._thinking_kind, secondi))


class Notice(QFrame):
    """Un avviso dentro la conversazione — una risposta non arrivata — con il suo rimedio."""

    def __init__(self, text: str, action: str = "", on_action=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("notice")
        self.setStyleSheet("QFrame#notice { border-left: 3px solid palette(highlight); }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 6, 6)
        etichetta = QLabel(text)
        etichetta.setWordWrap(True)
        etichetta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(etichetta, 1)
        self.button: QPushButton | None = None
        if action and on_action is not None:
            self.button = QPushButton(action)
            self.button.setMinimumHeight(44)
            self.button.clicked.connect(on_action)
            self.button.clicked.connect(lambda: self.button.setEnabled(False))
            lay.addWidget(self.button)


# --------------------------------------------------------------------------- #
#  la conversazione
# --------------------------------------------------------------------------- #
class ChatView(QScrollArea):
    """L'elenco delle battute, in una colonna centrata di larghezza leggibile.

    Scorre da sola in fondo quando arriva testo nuovo, ma solo se chi legge era già in fondo:
    chi è risalito a rileggere non deve essere strattonato giù a ogni parola.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName(t("Conversazione sul libro"))

        fondo = QWidget()
        fondo.setObjectName("chatBackground")
        fondo.setStyleSheet("QWidget#chatBackground { background: palette(base); }")
        esterno = QHBoxLayout(fondo)
        esterno.setContentsMargins(16, 16, 16, 16)
        esterno.addStretch(1)
        self._column = QWidget()
        self._column.setMaximumWidth(COLUMN_MAX)
        self._column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._rows = QVBoxLayout(self._column)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(14)
        self._rows.addStretch(1)
        esterno.addWidget(self._column, 100)
        esterno.addStretch(1)
        self.setWidget(fondo)

        self._stick = True
        barra = self.verticalScrollBar()
        barra.valueChanged.connect(self._remember_position)
        barra.rangeChanged.connect(self._follow)

    # ---- contenuto -----------------------------------------------------------
    def clear(self) -> None:
        while self._rows.count() > 1:
            _dispose(self._rows.takeAt(0))
        self._stick = True

    def add_message(self, role: str, text: str) -> Bubble:
        bolla = Bubble(role, text)
        if role == "user":
            riga = QHBoxLayout()
            riga.addStretch(1)
            riga.addWidget(bolla)
            self._insert(riga)
            bolla.prose.set_cap(self._user_cap())
        else:
            self._insert(bolla)
        return bolla

    def begin_reply(self, label: str = "") -> Bubble:
        """Una battuta dell'assistente ancora vuota, che si riempie man mano."""
        self._stick = True
        bolla = self.add_message("assistant", "")
        bolla.wait(label)
        return bolla

    def add_notice(self, text: str, action: str = "", on_action=None) -> Notice:
        avviso = Notice(text, action, on_action)
        self._insert(avviso)
        return avviso

    def bubbles(self) -> list[Bubble]:
        return self._column.findChildren(Bubble)

    def _insert(self, item) -> None:
        posizione = self._rows.count() - 1  # prima dello spazio elastico in fondo
        if isinstance(item, QWidget):
            self._rows.insertWidget(posizione, item)
        else:
            self._rows.insertLayout(posizione, item)

    # ---- misure e scorrimento -------------------------------------------------
    def _user_cap(self) -> int:
        larghezza = min(self.viewport().width() - 32, COLUMN_MAX)
        return int(max(240, larghezza) * USER_SHARE) - 30

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        tetto = self._user_cap()
        for bolla in self.bubbles():
            if bolla.role == "user":
                bolla.prose.set_cap(tetto)

    def _remember_position(self, valore: int) -> None:
        self._stick = valore >= self.verticalScrollBar().maximum() - 48

    def _follow(self, _minimo: int, massimo: int) -> None:
        if self._stick:
            self.verticalScrollBar().setValue(massimo)

    def scroll_to_end(self) -> None:
        self._stick = True
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


def _dispose(voce) -> None:
    """Toglie una riga della conversazione, e subito.

    ``deleteLater`` da solo lascia il widget figlio della colonna finché il ciclo degli eventi
    non lo distrugge: nel frattempo ``bubbles()`` lo conterebbe ancora, e ridisegnando la
    conversazione di un altro libro si vedrebbero le battute di quello di prima.
    """
    widget = voce.widget()
    if widget is not None:
        # Il riferimento si tiene prima: dopo ``setParent`` l'elemento di layout non lo ha più.
        widget.setParent(None)
        widget.deleteLater()
    elif voce.layout() is not None:
        while voce.layout().count():
            _dispose(voce.layout().takeAt(0))
        voce.layout().deleteLater()


# --------------------------------------------------------------------------- #
#  dove si scrive
# --------------------------------------------------------------------------- #
class _Input(QPlainTextEdit):
    """Invio manda; Maiuscolo+Invio va a capo, come in ogni chat."""

    submit = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        invio = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if invio and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submit.emit()
            return
        super().keyPressEvent(event)


class Composer(QFrame):
    """Il campo in cui l'autore scrive, con «Invia» e — mentre arriva una risposta — «Ferma»."""

    submitted = Signal(str)
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None, *, attach: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("composer")
        self.setStyleSheet(
            "QFrame#composer { background: palette(base); border: 1px solid palette(mid); "
            "border-radius: 14px; }"
        )
        self.attachments: list[Path] = []
        esterno = QVBoxLayout(self)
        esterno.setContentsMargins(12, 8, 8, 8)
        esterno.setSpacing(4)
        # I documenti allegati, sopra il campo: si vede cosa partirà e si può togliere.
        self.attached_row = QWidget()
        riga_allegati = QHBoxLayout(self.attached_row)
        riga_allegati.setContentsMargins(0, 0, 0, 0)
        self.attached_label = QLabel("")
        self.attached_label.setWordWrap(True)
        self.attached_label.setAccessibleName(t("Documenti allegati"))
        riga_allegati.addWidget(self.attached_label, 1)
        self.attached_clear = QPushButton(t("Togli gli allegati"))
        self.attached_clear.setMinimumHeight(44)
        self.attached_clear.clicked.connect(self.clear_attachments)
        riga_allegati.addWidget(self.attached_clear)
        self.attached_row.setVisible(False)
        esterno.addWidget(self.attached_row)
        lay = QHBoxLayout()
        esterno.addLayout(lay)
        self.input = _Input()
        self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.setPlaceholderText(t("Scrivi all'assistente… (Invio per mandare, "
                                        "Maiuscolo+Invio per andare a capo)"))
        self.input.setAccessibleName(t("Messaggio per l'assistente"))
        # Il segnaposto di serie è un grigio fisso che su un fondo scuro quasi sparisce: si
        # calcola dalla tavolozza, come ogni testo secondario (contrasto ≥ 4,5:1 nei due temi).
        self.input.setStyleSheet(
            f"QPlainTextEdit {{ placeholder-text-color: {muted_color(self)}; }}")
        self.input.setTabChangesFocus(True)
        reading_font(self.input)
        self.input.submit.connect(self._send)
        self.input.textChanged.connect(self._grow)
        lay.addWidget(self.input, 1)

        self.attach: QPushButton | None = None
        if attach:
            self.attach = QPushButton(t("Allega documento…"))
            self.attach.setToolTip(t("Allega un documento (pdf, docx, md, txt): l'assistente lo "
                                     "legge e ne ricava le fonti."))
            self.attach.setMinimumHeight(44)
            self.attach.clicked.connect(self._choose_files)
            lay.addWidget(self.attach, 0, Qt.AlignmentFlag.AlignBottom)

        self.stop = QPushButton(t("Ferma"))
        self.stop.setToolTip(t("Interrompe la risposta che sta arrivando. Quello che è già "
                               "arrivato resta nella conversazione."))
        self.stop.setMinimumSize(44, 44)
        self.stop.setVisible(False)
        self.stop.clicked.connect(lambda: self.stop_requested.emit())
        lay.addWidget(self.stop, 0, Qt.AlignmentFlag.AlignBottom)

        self.send = QPushButton(t("Invia"))
        self.send.setDefault(False)
        self.send.setMinimumSize(64, 44)
        self.send.clicked.connect(self._send)
        lay.addWidget(self.send, 0, Qt.AlignmentFlag.AlignBottom)
        self._grow()

    def text(self) -> str:
        return self.input.toPlainText()

    def clear(self) -> None:
        self.input.clear()
        self.clear_attachments()

    # ---- allegati ---------------------------------------------------------------
    def add_attachments(self, paths: list[Path | str]) -> None:
        for p in paths:
            percorso = Path(p)
            if percorso not in self.attachments:
                self.attachments.append(percorso)
        self._show_attachments()

    def clear_attachments(self) -> None:
        self.attachments = []
        self._show_attachments()

    def take_attachments(self) -> list[Path]:
        return list(self.attachments)

    def _choose_files(self) -> None:
        scelti, _ = QFileDialog.getOpenFileNames(self, t("Allega documento"), "",
                                                 t("Documenti (*.pdf *.docx *.md *.markdown *.txt);;"
                                                   "Tutti i file (*)"))
        if scelti:
            self.add_attachments(scelti)

    def _show_attachments(self) -> None:
        self.attached_row.setVisible(bool(self.attachments))
        self.attached_label.setText(
            t("Allegati: {0}").format(", ".join(p.name for p in self.attachments)))

    def set_streaming(self, on: bool) -> None:
        self.stop.setVisible(on)
        self.stop.setEnabled(on)

    def _send(self) -> None:
        testo = self.input.toPlainText().strip()
        if testo or self.attachments:
            self.submitted.emit(testo)

    def _grow(self) -> None:
        riga = self.input.fontMetrics().lineSpacing()
        righe = max(1, min(INPUT_MAX_LINES, int(self.input.document().size().height())))
        margini = int(self.input.document().documentMargin() * 2) + 6
        self.input.setFixedHeight(max(44, righe * riga + margini))


# --------------------------------------------------------------------------- #
#  tutto insieme: la conversazione e il quaderno
# --------------------------------------------------------------------------- #
class ChatPanel(QWidget):
    """La conversazione a sinistra, il quaderno del libro a destra, divisi da una maniglia."""

    def __init__(self, parent: QWidget | None = None, *, side_title: str = "",
                 side_placeholder: str = "", attach: bool = False) -> None:
        super().__init__(parent)
        self._placeholder = side_placeholder
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(True)

        sinistra = QWidget()
        sinistra.setObjectName("chatColumn")
        sinistra.setStyleSheet("QWidget#chatColumn { background: palette(base); }")
        colonna = QVBoxLayout(sinistra)
        colonna.setContentsMargins(0, 0, 0, 0)
        colonna.setSpacing(8)
        self.view = ChatView()
        colonna.addWidget(self.view, 1)
        piede = QHBoxLayout()
        piede.setContentsMargins(16, 0, 16, 8)
        piede.addStretch(1)
        self.composer = Composer(attach=attach)
        self.composer.setMaximumWidth(COLUMN_MAX)
        piede.addWidget(self.composer, 100)
        piede.addStretch(1)
        colonna.addLayout(piede)
        self.splitter.addWidget(sinistra)

        self.notebook_box = QWidget()
        destra = QVBoxLayout(self.notebook_box)
        destra.setContentsMargins(8, 0, 0, 8)
        testa = QHBoxLayout()
        self.side_title = QLabel(side_title or t("Quaderno del libro"))
        self.side_title.setStyleSheet("font-weight: bold;")
        testa.addWidget(self.side_title)
        testa.addStretch(1)
        self.notebook_status = QLabel("")
        self.notebook_status.setStyleSheet(f"color: {muted_color(self)}; font-style: italic;")
        testa.addWidget(self.notebook_status)
        destra.addLayout(testa)
        self.notebook = QTextBrowser()
        self.notebook.setOpenExternalLinks(True)
        self.notebook.setAccessibleName(side_title or t("Quaderno del libro"))
        reading_font(self.notebook, 1.15)
        destra.addWidget(self.notebook, 1)
        self.splitter.addWidget(self.notebook_box)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        lay.addWidget(self.splitter)
        self.set_notebook("")

    def set_notebook(self, markdown: str) -> None:
        """Il quaderno, in markdown. Titoli di secondo livello diventano di terzo: in una
        colonna stretta, titoli grandi come quelli di una pagina mangiano tutto lo spazio."""
        markdown = re.sub(r"(?m)^## ", "### ", markdown or "")
        if markdown.strip():
            self.notebook.setMarkdown(markdown)
        else:
            self.notebook.setMarkdown(self._placeholder or t(
                "*Qui si raccoglie quello che emerge dalla conversazione: la trama, i "
                "personaggi, i luoghi, quello che il libro vuole ottenere e le decisioni prese "
                "insieme. Si aggiorna dopo ogni scambio.*"))
        cursore = QTextCursor(self.notebook.document())
        cursore.select(QTextCursor.SelectionType.Document)
        formato = QTextBlockFormat()
        formato.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursore.mergeBlockFormat(formato)

    def set_notebook_status(self, text: str) -> None:
        self.notebook_status.setText(text)

    def show_notebook(self, on: bool) -> None:
        self.notebook_box.setVisible(on)
