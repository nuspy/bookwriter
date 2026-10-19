"""La finestra per aggiungere o modificare un server personalizzato.

Nome, indirizzo (la base dell'API, per esempio ``http://127.0.0.1:7232/v1``), tipo di API, chiave
facoltativa e livello di ragionamento che di solito vuole. «Prova» interroga ``/models`` e dice in
parole cosa ha trovato, o perché non ci è riuscito, senza scrivere niente nel registro.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from llmswitch import reasoning as lls_reasoning
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from bookwriter.gui.chat import muted_color
from bookwriter.gui.i18n import t
from bookwriter.gui.worker import Worker
from bookwriter.llm import servers as llm_servers


def reasoning_choices() -> list[tuple[str, str]]:
    """``(valore da salvare, etichetta)``: «lascia decidere al server» salva il vuoto, cioè non
    manda niente; gli altri sono i livelli di ``llmswitch.reasoning``."""
    etichette = {
        lls_reasoning.INHERIT: t("Lascia decidere al server"),
        "none": t("Nessuno"), "minimal": t("Minimo"), "low": t("Basso"), "medium": t("Medio"),
        "high": t("Alto"), "xhigh": t("Molto alto"), "max": t("Massimo"),
    }
    return [("" if k == lls_reasoning.INHERIT else k, etichette[k]) for k in lls_reasoning.CHOICES]


def structured_choices() -> list[tuple[str, str]]:
    """``(valore, etichetta)`` dell'«Output strutturato»: come si chiede il JSON al server."""
    return [("json_schema", t("JSON Schema")), ("json_object", t("JSON Object")), ("text", t("Testo"))]


def structured_tooltip() -> str:
    return t("Come si chiede al server una risposta in JSON. «JSON Schema»: lo schema nel formato "
             "della richiesta (predefinito). «JSON Object»: un JSON qualsiasi, con lo schema "
             "nel prompt. «Testo»: solo il prompt. In ogni caso il programma controlla e, se "
             "serve, ripara la risposta ricevuta.")


def reasoning_tooltip() -> str:
    return t("Quanto ragiona il modello prima di rispondere: più ragiona, più crescono la "
             "qualità e l'aderenza alle istruzioni, ma anche il tempo e i token consumati. "
             "«Lascia decidere al server» non manda nessun parametro. Con «Alto» un modello "
             "locale ha prodotto più testo ma ha consumato molti più token di ragionamento.")


class ServerDialog(QDialog):
    """Dati di un server personalizzato. ``values()`` li restituisce dopo ``accept``."""

    def __init__(self, parent=None, entry: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("Modifica il server") if entry else t("Aggiungi un server"))
        self.resize(560, 0)
        #: i test lo mettono a ``False`` per eseguire la prova senza thread
        self._threaded = True
        lay = QVBoxLayout(self)
        # I segnaposto si leggono anche su un fondo scuro (contrasto calcolato dalla tavolozza).
        self.setStyleSheet(f"QLineEdit {{ placeholder-text-color: {muted_color(self)}; }}")
        lay.addWidget(QLabel(t(
            "Un server che parla come OpenAI: un router, un llama-server, un servizio nel cloud "
            "compatibile. Si salva nel registro dei fornitori condiviso con gli altri tuoi "
            "programmi.")))
        form = QFormLayout()
        self.name = QLineEdit((entry or {}).get("name", ""))
        self.name.setPlaceholderText(t("per esempio: Il mio router"))
        self.name.setAccessibleName(t("Nome del server"))
        form.addRow(t("Nome:"), self.name)
        self.url = QLineEdit((entry or {}).get("base_url", ""))
        self.url.setPlaceholderText("http://127.0.0.1:7232/v1")
        self.url.setAccessibleName(t("Indirizzo del server"))
        form.addRow(t("Indirizzo:"), self.url)
        self.kind = QComboBox()
        for tipo, etichetta in llm_servers.type_choices():
            self.kind.addItem(etichetta, tipo)
        self.kind.setToolTip(t("Solo i tipi compatibili con OpenAI, gli unici che questo "
                               "programma sa interrogare."))
        if entry:
            self.kind.setCurrentIndex(max(0, self.kind.findData(entry.get("type", "openai"))))
            self.kind.setEnabled(False)  # il tipo non si cambia: sarebbe un altro server
        form.addRow(t("Tipo di API:"), self.kind)
        self.key = QLineEdit((entry or {}).get("api_key", ""))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(t("facoltativa"))
        self.key.setAccessibleName(t("Chiave del server"))
        form.addRow(t("Chiave:"), self.key)
        self.effort = QComboBox()
        for valore, etichetta in reasoning_choices():
            self.effort.addItem(etichetta, valore)
        self.effort.setToolTip(reasoning_tooltip())
        livello = (entry or {}).get("reasoning_level", lls_reasoning.INHERIT)
        livello = "" if livello == lls_reasoning.INHERIT else livello
        self.effort.setCurrentIndex(max(0, self.effort.findData(livello)))
        form.addRow(t("Ragionamento:"), self.effort)
        self.structured = QComboBox()
        for valore, etichetta in structured_choices():
            self.structured.addItem(etichetta, valore)
        self.structured.setToolTip(structured_tooltip())
        self.structured.setAccessibleName(t("Output strutturato"))
        self.structured.setCurrentIndex(max(0, self.structured.findData(
            (entry or {}).get("structured_output", "json_schema"))))
        form.addRow(t("Output strutturato:"), self.structured)
        lay.addLayout(form)

        self.result = QLabel("")
        self.result.setWordWrap(True)
        lay.addWidget(self.result)
        righe = QHBoxLayout()
        self.try_button = QPushButton(t("Prova"))
        self.try_button.setToolTip(t("Interroga il server e mostra i modelli che trova."))
        self.try_button.setMinimumHeight(44)
        self.try_button.clicked.connect(self._try)
        righe.addWidget(self.try_button)
        righe.addStretch(1)
        self.ok = QPushButton(t("Salva"))
        self.ok.setMinimumHeight(44)
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._accept)
        righe.addWidget(self.ok)
        cancel = QPushButton(t("Annulla"))
        cancel.setMinimumHeight(44)
        cancel.clicked.connect(self.reject)
        righe.addWidget(cancel)
        lay.addLayout(righe)

    # ---- dati -----------------------------------------------------------------
    def values(self) -> dict[str, Any]:
        return {"name": self.name.text().strip(), "base_url": self.url.text().strip(),
                "tipo": self.kind.currentData() or "openai", "api_key": self.key.text().strip(),
                "reasoning_level": self.effort.currentData() or lls_reasoning.INHERIT,
                "structured_output": self.structured.currentData() or "json_schema"}

    def problem(self) -> str:
        """Perché i dati non vanno, in parole; vuoto se vanno."""
        v = self.values()
        if not v["name"]:
            return t("Dai un nome al server.")
        if llm_servers.validate(v["name"], v["base_url"], v["tipo"]):
            return t("L'indirizzo deve cominciare con http:// o https:// "
                     "(per esempio http://127.0.0.1:7232/v1).")
        return ""

    def _accept(self) -> None:
        errore = self.problem()
        if errore:
            self.result.setText(errore)
            return
        self.accept()

    # ---- prova ----------------------------------------------------------------
    def _try(self) -> None:
        errore = self.problem()
        if errore and not self.url.text().strip():
            self.result.setText(errore)
            return
        v = self.values()
        self.result.setText(t("Provo…"))
        self.try_button.setEnabled(False)

        def done(esito: dict) -> None:
            self.try_button.setEnabled(True)
            if esito.get("ok"):
                elenco = ", ".join(esito.get("sample") or [])
                self.result.setText(t("Il server risponde: {0} modelli ({1}).").format(
                    esito.get("models", 0), elenco) if elenco else
                    t("Il server risponde, ma non elenca nessun modello."))
            else:
                self.result.setText(t("Il server non risponde: {0}").format(esito.get("error", "")))

        job: Callable[[], dict] = lambda: llm_servers.try_server(  # noqa: E731
            v["base_url"], v["tipo"], v["api_key"])
        if not self._threaded:
            done(job())
            return
        worker = Worker(job)
        worker.signals.finished.connect(done)
        worker.signals.error.connect(lambda m: done({"ok": False, "error": m}))
        QThreadPool.globalInstance().start(worker)
