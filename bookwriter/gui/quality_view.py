"""Le schede dei voti della verifica della qualità: una per criterio, da 0 a 10.

Ogni scheda dice il criterio, il voto in grande, il giudizio a parole («buono», «debole»…) e una
barra; i criteri critici per il tipo di libro hanno il segno «critico», quelli che non si
applicano restano visibili ma spenti. Il colore non è mai l'unico veicolo: il numero e la parola
dicono la stessa cosa. Le schede si possono scegliere col mouse e con la tastiera (Tab, Invio,
spazio): la scheda scelta mostra sotto la prova, i problemi e le correzioni.

La griglia si ridispone con la larghezza della finestra: una colonna in una finestra stretta,
fino a cinque in una larga.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from bookwriter.forge import quality as ql
from bookwriter.gui import theme
from bookwriter.gui.i18n import t

CARD_WIDTH = 230


def score_color(score: float | None) -> str:
    toni = theme.current_tones()
    if score is None:
        return toni.muted
    if score >= 8.5:
        return toni.done
    if score >= 7:
        return toni.accent
    if score >= 5.5:
        return toni.warn
    return toni.bad


class _Bar(QWidget):
    """La barra del voto: lo spazio pieno è il voto su dieci."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.score: float | None = None
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _ev) -> None:  # noqa: N802 - nome di Qt
        toni = theme.current_tones()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(toni.line))
        p.drawRoundedRect(self.rect(), 4, 4)
        if self.score is not None:
            pieno = self.rect().adjusted(0, 0, -int(self.width() * (1 - self.score / 10)), 0)
            p.setBrush(QColor(score_color(self.score)))
            p.drawRoundedRect(pieno, 4, 4)
        p.end()


class ScoreCard(QFrame):
    """La scheda di un criterio."""

    chosen = Signal(str)

    def __init__(self, voto: ql.Score, parent: QWidget | None = None, *, big: bool = False) -> None:
        super().__init__(parent)
        self.key = voto.key
        self.setObjectName("scoreCard")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(CARD_WIDTH - 20)
        self.setMinimumHeight(132 if big else 118)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        testa = QLabel(t(voto.label) + ("  ·  " + t("critico") if voto.critical else ""))
        testa.setWordWrap(True)
        testa.setObjectName("cardTitle")
        lay.addWidget(testa)
        numero = "—" if voto.score is None else f"{voto.score:.1f}"
        self.valore = QLabel(f"{numero}<span style='font-size:13px'> / 10</span>")
        self.valore.setTextFormat(Qt.TextFormat.RichText)
        self.valore.setObjectName("cardScore")
        lay.addWidget(self.valore)
        self.barra = _Bar()
        self.barra.score = voto.score
        lay.addWidget(self.barra)
        giudizio = t(ql.band(voto.score)) if voto.weight > 0 else t("non si applica")
        dettaglio = voto.measure or ""
        self.giudizio = QLabel(giudizio + (f" · {dettaglio}" if dettaglio else ""))
        self.giudizio.setWordWrap(True)
        self.giudizio.setObjectName("cardBand")
        lay.addWidget(self.giudizio)
        self.score = voto.score
        self.big = big
        self.setAccessibleName(f"{t(voto.label)}: {numero} / 10, {giudizio}")
        self.restyle()

    def restyle(self, selected: bool = False) -> None:
        toni = theme.current_tones()
        colore = score_color(self.score)
        bordo = toni.accent if selected else toni.line
        self.setStyleSheet(
            f"QFrame#scoreCard{{background:{toni.base};border:{2 if selected else 1}px solid "
            f"{bordo};border-radius:12px}}"
            f"QFrame#scoreCard:focus{{border:2px solid {toni.accent}}}"
            f"QLabel#cardTitle{{color:{toni.muted};font-weight:600;border:none}}"
            f"QLabel#cardScore{{color:{colore};font-size:{30 if self.big else 26}px;"
            f"font-weight:700;border:none}}"
            f"QLabel#cardBand{{color:{toni.text};border:none}}")

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        self.chosen.emit(self.key)
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.chosen.emit(self.key)
            return
        super().keyPressEvent(ev)


class ScoreGrid(QWidget):
    """Le schede in griglia, ridisposte con la larghezza."""

    chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.griglia = QGridLayout(self)
        self.griglia.setContentsMargins(0, 0, 0, 0)
        self.griglia.setSpacing(10)
        self.cards: list[ScoreCard] = []
        self._colonne = 0

    def set_scores(self, voti: list[ql.Score], *, big_first: bool = False) -> None:
        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards = []
        for i, v in enumerate(voti):
            card = ScoreCard(v, self, big=big_first and i == 0)
            card.chosen.connect(self._choose)
            self.cards.append(card)
        self._colonne = 0
        self._reflow()

    def _choose(self, chiave: str) -> None:
        for c in self.cards:
            c.restyle(selected=c.key == chiave)
        self.chosen.emit(chiave)

    def restyle(self) -> None:
        for c in self.cards:
            c.restyle()
            c.barra.update()

    def _reflow(self) -> None:
        colonne = max(1, min(5, self.width() // CARD_WIDTH)) if self.width() > 0 else 3
        if colonne == self._colonne:
            return
        self._colonne = colonne
        for c in self.cards:
            self.griglia.removeWidget(c)
        for i, c in enumerate(self.cards):
            self.griglia.addWidget(c, i // colonne, i % colonne)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._reflow()


def _globale(valutazione: ql.Evaluation) -> ql.Score:
    g = valutazione.global_score
    return ql.Score("globale", t("Valutazione globale"), g, 1.0, False,
                    measure=t("{0} parole").format(valutazione.words) if valutazione.words else "")


class QualityPanel(QWidget):
    """Il voto globale, le schede dei criteri e, sotto, il dettaglio della scheda scelta."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.titolo = QLabel("")
        self.titolo.setWordWrap(True)
        self.titolo.setObjectName("qualityTitle")
        lay.addWidget(self.titolo)
        self.grid = ScoreGrid(self)
        self.grid.chosen.connect(self._show_detail)
        lay.addWidget(self.grid)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.detail.setMinimumHeight(160)
        self.detail.setAccessibleName(t("Dettaglio del criterio scelto"))
        lay.addWidget(self.detail, 1)
        self.valutazione: ql.Evaluation | None = None
        self.soglie: ql.Thresholds | None = None
        theme.on_change(self.grid.restyle)

    def clear(self, testo: str = "") -> None:
        self.valutazione = None
        self.titolo.setText(testo)
        self.grid.set_scores([])
        self.detail.setHtml("")

    def show_evaluation(self, valutazione: ql.Evaluation, soglie: ql.Thresholds) -> None:
        self.valutazione, self.soglie = valutazione, soglie
        esito = t("qualità accettata") if valutazione.accepted(soglie) else \
            t("sotto la soglia (globale {0}, critici {1})").format(soglie.min_global,
                                                                    soglie.min_critical)
        tipo = t(ql.KINDS.get(valutazione.kind, valutazione.kind))
        self.titolo.setText(f"<b>{html.escape(valutazione.title)}</b> · {html.escape(tipo)} · "
                            f"{html.escape(esito)}")
        self.grid.set_scores([_globale(valutazione), *valutazione.scores], big_first=True)
        self._show_detail("globale")

    def _show_detail(self, chiave: str) -> None:
        v = self.valutazione
        if v is None:
            return
        e = html.escape
        if chiave == "globale":
            righe = [f"<h3>{e(t('Valutazione globale'))}: {v.global_score}</h3>"]
            if v.summary:
                righe.append(f"<p>{e(v.summary)}</p>")
            if v.authors:
                righe.append(f"<p><b>{e(t('Lo stile ricorda'))}:</b></p><ul>" + "".join(
                    f"<li><b>{e(a['author'])}</b> — {e(a.get('why', ''))}</li>"
                    for a in v.authors) + "</ul>")
            if v.comparables:
                righe.append(f"<p><b>{e(t('Titoli di confronto'))}:</b> "
                             + e("; ".join(v.comparables)) + "</p>")
            if v.audience:
                righe.append(f"<p><b>{e(t('Pubblico'))}:</b> {e(v.audience)}</p>")
            if v.risks:
                righe.append(f"<p><b>{e(t('Rischi'))}:</b></p><ul>"
                             + "".join(f"<li>{e(r)}</li>" for r in v.risks) + "</ul>")
            if v.priorities:
                righe.append(f"<p><b>{e(t('Le correzioni che contano di più'))}:</b></p><ol>"
                             + "".join(f"<li>{e(r)}</li>" for r in v.priorities) + "</ol>")
            sotto = v.failing(self.soglie) if self.soglie else []
            if sotto:
                righe.append(f"<p><b>{e(t('Sotto soglia'))}:</b> "
                             + e(", ".join(f"{t(s.label)} ({s.score})" for s in sotto)) + "</p>")
            if v.errors:
                righe.append("<p><i>" + e("; ".join(v.errors)) + "</i></p>")
            self.detail.setHtml("".join(righe))
            return
        s = v.score(chiave)
        if s is None:
            return
        righe = [f"<h3>{e(t(s.label))}: {'—' if s.score is None else s.score}"
                 f"{' · ' + e(t('critico')) if s.critical else ''}</h3>"]
        if chiave == "potenziale":
            righe.append(f"<p><i>{e(t('È una stima motivata, non una previsione.'))}</i></p>")
        if s.verdict:
            righe.append(f"<p>{e(s.verdict)}</p>")
        if s.evidence:
            righe.append(f"<blockquote>«{e(s.evidence)}»</blockquote>")
        if s.problems:
            righe.append(f"<p><b>{e(t('Problemi'))}:</b></p><ol>")
            for p in s.problems:
                dove = (t("sezione {0}").format(p.section + 1) if p.section >= 0
                        else t("tutto il capitolo"))
                righe.append(f"<li><b>{e(p.problem)}</b> ({e(p.severity)}, {e(dove)})"
                             + (f"<br>«{e(p.quote)}»" if p.quote else "")
                             + (f"<br>→ {e(p.fix)}" if p.fix else "") + "</li>")
            righe.append("</ol>")
        self.detail.setHtml("".join(righe))
