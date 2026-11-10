"""L'aspetto della finestra: palette chiara e scura, un colore per ogni lavoro, icone.

Una sola fonte per i colori. I pannelli usano i ruoli della palette (``palette(mid)``,
``palette(highlight)``…) e non colori scritti a mano: cambiando la palette cambia tutto, e il
tema scuro non lascia in giro scritte grigie su grigio.

Tre modi: **automatico** (segue il sistema e cambia con lui), **chiaro**, **scuro**. Un solo
colore d'accento, sobrio: le schede sono neutre, si schiariscono al passaggio del mouse e
prendono l'accento quando sono aperte; le fasi completate di un lavoro prendono il colore del
«fatto». Le icone sono lineari e monocromatiche (il set di sistema di Windows, Segoe Fluent
Icons), dello stesso colore del testo, e diventano d'accento sulla scheda aperta. I contrasti dei
testi restano almeno 4,5:1 (vedi :func:`check_contrast`), il fuoco della tastiera si vede, i
bersagli sono alti almeno 44 px.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QTabWidget

THEMES = ("auto", "light", "dark")


@dataclass(frozen=True)
class Tones:
    window: str
    base: str
    alt: str
    text: str
    muted: str
    line: str
    accent: str
    on_accent: str
    button: str
    tooltip: str
    done: str  #: il colore di una fase completata
    soft: str  #: lo sfondo tenue dell'accento (passaggio del mouse, selezione leggera)
    warn: str = "#8A5A00"  #: un voto discreto (la verifica della qualità)
    bad: str = "#B3261E"  #: un voto debole


LIGHT = Tones(window="#F4F6F9", base="#FFFFFF", alt="#EEF1F5", text="#1B2330", muted="#566173",
              line="#D6DCE5", accent="#1F5FBF", on_accent="#FFFFFF", button="#FFFFFF",
              tooltip="#1B2330", done="#0F6E66", soft="#E6EEF9")
DARK = Tones(window="#111620", base="#171D29", alt="#1F2634", text="#E6EAF1", muted="#A3ADBE",
             line="#2C3546", accent="#6EA8FE", on_accent="#0A1220", button="#1F2634",
             tooltip="#E6EAF1", done="#5CC8B8", soft="#1C2B44", warn="#E8B96A", bad="#F2A49E")

#: L'icona di ogni lavoro (chiave della scheda di primo livello): un glifo di Segoe Fluent Icons
#: (Windows 11) o Segoe MDL2 Assets (Windows 10), lo stesso codice nei due; altrove, un simbolo.
ICONS: dict[str, tuple[str, str]] = {
    "notes": ("\uE70B", "✎"),       # QuickNote
    "scratch": ("\uE70F", "✐"),     # Edit
    "complete": ("\uE8A5", "▤"),    # Document
    "update": ("\uE895", "⟳"),      # Sync
    "rewrite": ("\uE8D2", "A"),     # Font
    "translate": ("\uE774", "◎"),   # Globe
    "style": ("\uE790", "◐"),       # Color
    "read": ("\uE736", "▭"),        # ReadingMode
    "quality": ("\uE9D9", "✓"),     # Diagnostic
    "utility": ("\uE713", "⚙"),     # Setting
}

ICON_SIZE = 24


def system_is_dark() -> bool:
    """Se il **sistema** chiede il tema scuro.

    Su Windows si legge l'impostazione del sistema e non quella che Qt deduce: Qt, dopo che
    l'app ha messo la sua palette, risponde con lo schema della palette — e il modo automatico
    resterebbe incollato al primo tema scelto.
    """
    import sys

    if sys.platform == "win32":
        try:
            import winreg

            chiave = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            valore, _ = winreg.QueryValueEx(chiave, "AppsUseLightTheme")
            return int(valore) == 0
        except OSError:
            return False
    app = QApplication.instance()
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def tones_for(theme: str) -> Tones:
    if theme == "dark":
        return DARK
    if theme == "light":
        return LIGHT
    return DARK if system_is_dark() else LIGHT


def palette_for(t: Tones) -> QPalette:
    p = QPalette()
    ruoli = {
        QPalette.ColorRole.Window: t.window, QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.base, QPalette.ColorRole.AlternateBase: t.alt,
        QPalette.ColorRole.Text: t.text, QPalette.ColorRole.Button: t.button,
        QPalette.ColorRole.ButtonText: t.text, QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: t.on_accent, QPalette.ColorRole.Link: t.accent,
        QPalette.ColorRole.ToolTipBase: t.base, QPalette.ColorRole.ToolTipText: t.tooltip,
        QPalette.ColorRole.PlaceholderText: t.muted, QPalette.ColorRole.Mid: t.muted,
        QPalette.ColorRole.Midlight: t.line, QPalette.ColorRole.Light: t.base,
        QPalette.ColorRole.Dark: t.line, QPalette.ColorRole.Shadow: t.line,
        QPalette.ColorRole.BrightText: t.on_accent,
    }
    for ruolo, colore in ruoli.items():
        p.setColor(ruolo, QColor(colore))
    for ruolo in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                  QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, ruolo, QColor(t.muted))
    return p


def stylesheet(t: Tones) -> str:
    """Il foglio di stile dell'applicazione: forme morbide, gerarchia chiara, niente spigoli."""
    return f"""
QWidget {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue",
           sans-serif; font-size: 10.5pt; }}
QMainWindow, QDialog {{ background: {t.window}; }}
QToolTip {{ background: {t.base}; color: {t.text}; border: 1px solid {t.line};
            border-radius: 8px; padding: 6px 8px; }}

QTabWidget#mainTabs::pane {{ border: none; border-top: 1px solid {t.line}; top: -1px; }}
QTabWidget#mainTabs > QTabBar::tab {{
    background: transparent; color: {t.text}; padding: 9px 16px; margin: 6px 4px 0 0;
    border: 1px solid transparent; border-top-left-radius: 12px; border-top-right-radius: 12px;
    min-height: 26px; }}
QTabWidget#mainTabs > QTabBar::tab:hover {{ background: {t.soft}; color: {t.accent}; }}
QTabWidget#mainTabs > QTabBar::tab:selected {{
    background: {t.base}; color: {t.accent}; border: 1px solid {t.line};
    border-bottom: 3px solid {t.accent}; font-weight: 600; }}
QTabWidget#mainTabs > QTabBar::tab:focus {{ outline: none; border: 2px solid {t.accent}; }}
QTabWidget::pane {{ border: 1px solid {t.line}; border-radius: 10px; top: -1px;
                   background: {t.base}; }}
QTabBar::tab {{ background: transparent; color: {t.muted}; padding: 7px 14px; margin-right: 4px;
               border: none; border-bottom: 2px solid transparent; min-height: 28px; }}
QTabBar::tab:hover {{ color: {t.accent}; background: {t.soft}; border-radius: 8px; }}
QTabBar::tab:selected {{ color: {t.text}; font-weight: 600; border-bottom: 2px solid {t.accent}; }}
QTabWidget#subTabs::pane {{ border: none; }}
QTabWidget#subTabs > QTabBar::tab {{
    background: transparent; color: {t.muted}; padding: 7px 14px; margin: 8px 4px 6px 0;
    border: none; border-bottom: 2px solid transparent; min-height: 30px; }}
QTabWidget#subTabs > QTabBar::tab:hover {{ color: {t.accent}; background: {t.soft};
    border-radius: 8px; }}
QTabWidget#subTabs > QTabBar::tab:selected {{ color: {t.text}; font-weight: 600;
    border-bottom: 2px solid {t.accent}; }}

QPushButton {{ background: {t.button}; color: {t.text}; border: 1px solid {t.line};
               border-radius: 10px; padding: 8px 14px; min-height: 28px; }}
QPushButton:hover {{ border-color: {t.accent}; background: {t.alt}; }}
QPushButton:pressed {{ background: {t.line}; }}
QPushButton:focus {{ border: 2px solid {t.accent}; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.alt}; border-color: {t.line}; }}
QPushButton:checked {{ background: {t.accent}; color: {t.on_accent}; border-color: {t.accent}; }}

QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox,
QListWidget, QTreeWidget, QTableWidget, QListView, QTreeView {{
    background: {t.base}; color: {t.text}; border: 1px solid {t.line}; border-radius: 10px;
    selection-background-color: {t.accent}; selection-color: {t.on_accent}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ padding: 6px 10px; min-height: 30px; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QListWidget:focus, QTreeWidget:focus, QTableWidget:focus {{
    border: 2px solid {t.accent}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ border: 1px solid {t.line}; border-radius: 8px;
    background: {t.base}; selection-background-color: {t.accent}; }}
QHeaderView::section {{ background: {t.alt}; color: {t.text}; border: none;
    border-bottom: 1px solid {t.line}; padding: 6px 8px; font-weight: 600; }}

QGroupBox {{ background: {t.base}; border: 1px solid {t.line}; border-radius: 14px;
             margin-top: 16px; padding: 14px 12px 10px 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; color: {t.text};
                    font-weight: 600; }}

QProgressBar {{ background: {t.alt}; border: none; border-radius: 7px; min-height: 14px;
                text-align: center; color: {t.text}; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.line}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.line}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {t.base}; border-top: 1px solid {t.line}; color: {t.muted}; }}
QSplitter::handle {{ background: {t.line}; }}
"""


#: Il tema scelto e i toni applicati adesso: per non riapplicare quello che c'è già.
_scelto = "auto"
_applicati: Tones | None = None
_in_corso = False


def apply(theme: str = "auto") -> Tones:
    """Applica il tema all'applicazione intera. Ritorna i toni scelti.

    Non rientra: applicare la palette fa emettere a Qt il cambio di schema, che chiamerebbe di
    nuovo questa funzione — un giro senza fine. E se i toni sono già quelli, non tocca niente.
    """
    global _scelto, _applicati, _in_corso
    app = QApplication.instance()
    if app is None:
        return LIGHT
    _scelto = theme if theme in THEMES else "auto"
    toni = tones_for(_scelto)
    if _in_corso or toni == _applicati:
        return toni
    _in_corso = True
    try:
        app.setStyle("Fusion")  # disegna da sé: la palette vale uguale su ogni sistema
        app.setPalette(palette_for(toni))
        app.setStyleSheet(stylesheet(toni))
        _applicati = toni
        recolor_icons()
        for richiamo in list(_listeners):
            try:
                richiamo()
            except RuntimeError:  # la finestra che l'aveva chiesto non c'è più
                _listeners.remove(richiamo)
    finally:
        _in_corso = False
    return toni


#: Chi deve ridisegnare qualcosa quando il tema cambia (colori calcolati, non di palette).
_listeners: list = []


def on_change(callback) -> None:
    """Registra chi ridisegna i suoi colori calcolati a ogni cambio di tema."""
    if callback not in _listeners:
        _listeners.append(callback)


def _system_changed(*_args) -> None:
    if _scelto == "auto" and not _in_corso:
        apply("auto")


def follow_system(callback=None) -> None:
    """In automatico, la finestra segue il sistema quando passa fra chiaro e scuro. Si collega
    una volta sola per applicazione, qualunque numero di finestre si apra."""
    app = QApplication.instance()
    if app is None or app.property("bw_follows_system"):
        return
    try:
        app.styleHints().colorSchemeChanged.connect(_system_changed)
        app.setProperty("bw_follows_system", True)
    except AttributeError:
        pass


# --------------------------------------------------------------------------- #
#  le icone e i colori delle schede
# --------------------------------------------------------------------------- #
def _icon_font() -> str:
    from PySide6.QtGui import QFontDatabase

    famiglie = set(QFontDatabase.families())
    for nome in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if nome in famiglie:
            return nome
    return ""


def line_icon(key: str, color: str, size: int = ICON_SIZE) -> QIcon:
    """L'icona di un lavoro: lineare, monocromatica, nel colore dato."""
    glifo, ripiego = ICONS.get(key, ("", "•"))
    famiglia = _icon_font()
    icona = QIcon()
    for scala in (1, 2):
        lato = size * scala
        pix = QPixmap(lato, lato)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        carattere = QFont(famiglia) if famiglia else QFont("Segoe UI Symbol")
        carattere.setPixelSize(int(lato * (0.78 if famiglia else 0.7)))
        p.setFont(carattere)
        p.setPen(QColor(color))
        p.drawText(QRectF(0, 0, lato, lato), int(Qt.AlignmentFlag.AlignCenter),
                   glifo if famiglia else ripiego)
        p.end()
        pix.setDevicePixelRatio(scala)
        icona.addPixmap(pix)
    return icona


def current_tones() -> Tones:
    return _applicati or tones_for(_scelto)


def decorate_tabs(tabs: QTabWidget, keys: list[str], subtabs: dict[int, QTabWidget]) -> None:
    """Le icone delle schede: del colore del testo attenuato, d'accento sulla scheda aperta."""
    from PySide6.QtCore import QSize

    tabs.setIconSize(QSize(ICON_SIZE, ICON_SIZE))

    def colora(indice: int) -> None:
        toni = current_tones()
        for i, chiave in enumerate(keys):
            tabs.setTabIcon(i, line_icon(chiave, toni.accent if i == indice else toni.muted))

    tabs.currentChanged.connect(colora)
    tabs.setProperty("bw_recolor", True)
    colora(tabs.currentIndex())
    _decorated.append((tabs, colora))


#: Le barre già decorate: al cambio di tema le icone si ridipingono nei colori nuovi.
_decorated: list = []


def recolor_icons() -> None:
    vive = []
    for tabs, colora in _decorated:
        try:
            colora(tabs.currentIndex())
            vive.append((tabs, colora))
        except RuntimeError:  # la barra non c'è più (finestra chiusa, lingua cambiata)
            continue
    _decorated[:] = vive


def phase_qss(state: str) -> str:
    """Il passo di un lavoro (le fasi): fatto, in corso, da fare. Stessa forma delle schede."""
    t = current_tones()
    base = "QPushButton{border-radius:10px;padding:6px 12px;min-height:30px;"
    if state == "fatta":
        return (base + f"color:{t.done};border:1px solid {t.done};background:transparent;"
                "font-weight:600}" + f"QPushButton:hover{{background:{t.soft}}}")
    if state == "in corso":
        return (base + f"color:{t.on_accent};background:{t.accent};border:1px solid "
                f"{t.accent};font-weight:600}}")
    return (base + f"color:{t.muted};background:transparent;border:1px solid {t.line}}}"
            + f"QPushButton:hover{{color:{t.accent};border-color:{t.accent}}}")


# --------------------------------------------------------------------------- #
#  il controllo dei contrasti
# --------------------------------------------------------------------------- #
def contrast(a: str, b: str) -> float:
    def lum(c: str) -> float:
        canali = [int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in canali]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def check_contrast(t: Tones) -> dict[str, float]:
    """I contrasti che contano: testo e testo attenuato sugli sfondi, testo sull'accento."""
    return {
        "testo/finestra": contrast(t.text, t.window), "testo/base": contrast(t.text, t.base),
        "attenuato/finestra": contrast(t.muted, t.window),
        "attenuato/base": contrast(t.muted, t.base),
        "attenuato/alternativo": contrast(t.muted, t.alt),
        "su accento": contrast(t.on_accent, t.accent),
        "accento/base": contrast(t.accent, t.base),
        "fatto/finestra": contrast(t.done, t.window),
        "discreto/base": contrast(t.warn, t.base),
        "debole/base": contrast(t.bad, t.base),
    }
