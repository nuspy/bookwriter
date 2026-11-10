"""La cronologia di un capitolo: chi è dove, a che ora, con che cosa, dietro quale porta.

La seconda analisi editoriale del cap. 11 ha trovato scene migliori montate in una sequenza
impossibile: il protagonista sottrae l'ultima pagina di un fascicolo e più avanti ne sottrae
un'altra «finale» senza che si capisca se è la stessa; resta chiuso a chiave in una stanza alle
2:10 e alle 2:45 è in giardino senza che si veda come è uscito; la cassa parte a mezzanotte in una
scena e alle undici nell'altra; una «fine primavera» convive con un ordine datato 2019-11-04; e il
capitolo comincia in Francia mentre il precedente lo lasciava in Egitto con un appuntamento prima
del tramonto. Un revisore che legge per lo stile non se ne accorge. Un registro degli eventi sì.

Qui il capitolo si legge **scena per scena**, in ordine, portandosi dietro uno stato: dove e
quando è il protagonista, chi c'è, quali oggetti e documenti ci sono e chi li ha (originale,
copia, sottratto…), quali porte sono chiuse e chi ha la chiave, quali scadenze sono fissate e a
che ora, quali date e quale stagione. Per ogni scena il modello registra i fatti nuovi
(:func:`record_scene`, una chiamata breve: sta nell'output dei modelli locali) e segnala ciò che
contraddice lo stato. Poi i **controlli deterministici** (:func:`check`) guardano quello che si può
misurare senza modello: orari che tornano indietro, uno spostamento o una fuga non mostrati, lo
stesso oggetto sottratto due volte, una scadenza con due orari, una data che non va con la
stagione. Infine una lettura del capitolo intero (:func:`threads_and_anticipations`) controlla
cosa ne è dei fili aperti del capitolo prima e se il capitolo anticipa eventi dei successivi.

Lo stesso registro, fatto durante la stesura, dà a ogni scena i **fatti già fissati** dalle scene
prima (:func:`facts_block`): la scena 4 sa che la pagina finale è già stata presa nella scena 1.

E prima ancora di scrivere, :func:`check_plan` guarda il piano delle scene con gli stessi occhi.

Niente qui verifica la storia reale o le invenzioni dell'autore: si controlla la coerenza
**interna** del racconto.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bookwriter.forge.metrics import content_words

BLOCKING, IMPORTANT, MINOR = "bloccante", "importante", "minore"
SEVERITIES = (BLOCKING, IMPORTANT, MINOR)

OBJECT_STATUS = ("con_se", "visto", "sottratto", "copiato", "fotografato", "consegnato",
                 "lasciato", "distrutto", "perso")
ACCESS_STATE = ("aperta", "chiusa", "chiusa_a_chiave", "forzata")

#: Quante segnalazioni del piano si passano al planner da riparare.
MAX_PLAN_ISSUES = 8

_T = {"type": "string"}
_L = {"type": "array", "items": {"type": "string"}}
_ISSUE = {"type": "object", "additionalProperties": False,
          "properties": {"severity": {"type": "string", "enum": list(SEVERITIES)},
                         "kind": _T, "problem": _T, "quote": _T, "fix": _T},
          "required": ["severity", "kind", "problem", "quote", "fix"]}

SCENE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "flashback": {"type": "boolean"},
        "day": _T, "time_start": _T, "time_end": _T,
        "place_start": _T, "place_end": _T,
        "arrival": _T,
        "present": _L,
        "events": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"time": _T, "what": _T}, "required": ["time", "what"]}},
        "objects": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": _T, "what": _T, "holder": _T,
                           "status": {"type": "string", "enum": list(OBJECT_STATUS)}},
            "required": ["id", "what", "holder", "status"]}},
        "access": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": _T, "what": _T,
                           "state": {"type": "string", "enum": list(ACCESS_STATE)},
                           "key_holder": _T},
            "required": ["id", "what", "state", "key_holder"]}},
        "locked_in_at_end": {"type": "boolean"},
        "deadlines": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": _T, "what": _T, "when": _T},
            "required": ["id", "what", "when"]}},
        "dates": _L,
        "season": _T,
        "knowledge": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"who": _T, "what": _T, "source": _T},
            "required": ["who", "what", "source"]}},
        "issues": {"type": "array", "items": _ISSUE},
    },
    "required": ["flashback", "day", "time_start", "time_end", "place_start", "place_end",
                 "arrival", "present", "events", "objects", "access", "locked_in_at_end",
                 "deadlines", "dates", "season", "knowledge", "issues"],
}


@dataclass
class Issue:
    severity: str = MINOR
    kind: str = ""
    problem: str = ""
    quote: str = ""
    fix: str = ""
    #: l'indice della sezione del capitolo (-1 = tutto il capitolo)
    section: int = -1
    #: chi l'ha trovata: ``modello`` o ``regola`` (controllo deterministico)
    origin: str = "modello"

    @property
    def serious(self) -> bool:
        return self.severity in (BLOCKING, IMPORTANT)

    def line(self) -> str:
        dove = f"scena {self.section + 1}" if self.section >= 0 else "capitolo"
        testo = f"[{self.severity}] {dove} — {self.problem}"
        if self.quote:
            testo += f" («{self.quote[:120]}»)"
        if self.fix:
            testo += f" → {self.fix}"
        return testo

    def instruction(self) -> str:
        """Il problema come indicazione per chi rivede la scena."""
        testo = f"continuità ({self.kind or 'eventi'}): {self.problem}"
        if self.quote:
            testo += f" — nel testo: «{self.quote[:200]}»"
        if self.fix:
            testo += f" — correzione minima: {self.fix}"
        return testo


@dataclass
class SceneRecord:
    heading: str = ""
    flashback: bool = False
    day: str = ""
    time_start: str = ""
    time_end: str = ""
    place_start: str = ""
    place_end: str = ""
    arrival: str = ""
    present: list[str] = field(default_factory=list)
    events: list[dict[str, str]] = field(default_factory=list)
    objects: list[dict[str, str]] = field(default_factory=list)
    access: list[dict[str, str]] = field(default_factory=list)
    locked_in_at_end: bool = False
    deadlines: list[dict[str, str]] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    season: str = ""
    #: chi viene a sapere cosa in questa scena, e da chi o da quale prova
    knowledge: list[dict[str, str]] = field(default_factory=list)
    #: vuoto se la lettura è riuscita
    error: str = ""


@dataclass
class Timeline:
    records: list[SceneRecord] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def serious(self) -> list[Issue]:
        return [i for i in self.issues if i.serious]

    def markdown(self) -> str:
        if not self.records:
            return "—"
        righe = ["| scena | quando | dove | chi | oggetti | porte |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(self.records):
            quando = " → ".join(x for x in (r.time_start, r.time_end) if x) or "?"
            if r.day:
                quando = f"{r.day}, {quando}"
            if r.flashback:
                quando = f"(episodio) {quando}"
            dove = r.place_start + (f" → {r.place_end}" if r.place_end and
                                    r.place_end != r.place_start else "")
            oggetti = "; ".join(f"{o.get('what', '')} ({o.get('status', '')}, "
                                f"{o.get('holder', '')})" for o in r.objects[:6])
            porte = "; ".join(f"{a.get('what', '')}: {a.get('state', '')}" for a in r.access[:4])
            if r.locked_in_at_end:
                porte += " — **chiuso dentro**"
            righe.append(f"| {i + 1} | {_cella(quando)} | {_cella(dove)} | "
                         f"{_cella(', '.join(r.present[:6]))} | {_cella(oggetti)} | "
                         f"{_cella(porte)} |")
        return "\n".join(righe)


def _cella(testo: str) -> str:
    return (testo or "").replace("|", "/").replace("\n", " ")[:160]


def timeline_path(project, number: int, title: str, *, trial: bool = False) -> Path:
    from bookwriter.forge.project import slugify

    prefisso = "prova_" if trial else ""
    return project.story_dir / "timeline" / f"{prefisso}{number:02d}_{slugify(title)}.json"


def save(path: Path, cronologia: Timeline) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cronologia), ensure_ascii=False, indent=1),
                    encoding="utf-8")


def load(path: Path) -> Timeline | None:
    """Una cronologia salvata da :func:`save`; None se manca o non si legge."""
    try:
        dati = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(dati, dict):
        return None
    campi = Issue.__dataclass_fields__
    return Timeline(
        records=[record_from_dict(r) for r in dati.get("records") or [] if isinstance(r, dict)],
        issues=[Issue(**{k: v for k, v in i.items() if k in campi})
                for i in dati.get("issues") or [] if isinstance(i, dict)])


def record_from_dict(d: dict[str, Any]) -> SceneRecord:
    campi = SceneRecord.__dataclass_fields__
    r = SceneRecord(**{k: v for k, v in d.items() if k in campi})
    r.present = [str(x) for x in (r.present or []) if str(x).strip()]
    r.dates = [str(x) for x in (r.dates or []) if str(x).strip()]
    for nome, chiavi in (("events", ("time", "what")),
                         ("objects", ("id", "what", "holder", "status")),
                         ("access", ("id", "what", "state", "key_holder")),
                         ("deadlines", ("id", "what", "when")),
                         ("knowledge", ("who", "what", "source"))):
        setattr(r, nome, [{k: str(v.get(k) or "").strip() for k in chiavi}
                          for v in (getattr(r, nome) or []) if isinstance(v, dict)])
    for o in r.objects:
        o["id"] = _ident(o["id"] or o["what"])
    for a in r.access:
        a["id"] = _ident(a["id"] or a["what"])
    for s in r.deadlines:
        s["id"] = _ident(s["id"] or s["what"])
    return r


def _ident(testo: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (testo or "").lower()).strip("_")[:48]


# --------------------------------------------------------------------------- #
#  lo stato che si porta avanti da una scena all'altra
# --------------------------------------------------------------------------- #
def _state_text(precedenti: list[SceneRecord], inizio: list[str]) -> str:
    """Lo stato prima della scena: il capitolo prima, poi quello che le scene hanno fissato."""
    righe = []
    if inizio:
        righe += ["## Dove il capitolo precedente ha lasciato il protagonista", *inizio, ""]
    if not precedenti:
        return "\n".join(righe + ["(è la prima scena del capitolo)"])
    righe.append("## Fissato dalle scene prima, in questo capitolo")
    presente = [r for r in precedenti if not r.flashback]
    if presente:
        ultimo = presente[-1]
        righe.append(f"- Protagonista, alla fine dell'ultima scena del presente: "
                     f"{ultimo.place_end or ultimo.place_start or '?'}, "
                     f"{ultimo.day + ', ' if ultimo.day else ''}"
                     f"{ultimo.time_end or ultimo.time_start or '?'}"
                     + (" — CHIUSO DENTRO" if ultimo.locked_in_at_end else ""))
    oggetti: dict[str, tuple[int, dict[str, str]]] = {}
    porte: dict[str, tuple[int, dict[str, str]]] = {}
    scadenze: dict[str, tuple[int, dict[str, str]]] = {}
    for k, r in enumerate(precedenti):
        for o in r.objects:
            oggetti[o["id"]] = (k, o)
        for a in r.access:
            porte[a["id"]] = (k, a)
        for s in r.deadlines:
            scadenze.setdefault(s["id"], (k, s))  # vale la prima ora fissata
    for k, o in oggetti.values():
        righe.append(f"- oggetto [{o['id']}] {o['what']}: {o['status']}, "
                     f"ha: {o['holder'] or '?'} (scena {k + 1})")
    for k, a in porte.values():
        righe.append(f"- accesso [{a['id']}] {a['what']}: {a['state']}"
                     + (f", chiave: {a['key_holder']}" if a.get("key_holder") else "")
                     + f" (scena {k + 1})")
    for k, s in scadenze.values():
        righe.append(f"- scadenza [{s['id']}] {s['what']}: {s['when']} (scena {k + 1})")
    date = [d for r in precedenti for d in r.dates]
    stagioni = [r.season for r in precedenti if r.season and not r.flashback]
    if date:
        righe.append("- date nel testo: " + "; ".join(dict.fromkeys(date)))
    if stagioni:
        righe.append(f"- stagione: {stagioni[-1]}")
    return "\n".join(righe)


def facts_block(precedenti: list[SceneRecord], inizio: list[str] | None = None) -> str:
    """I fatti fissati dalle scene già scritte, per la scena da scrivere: restano veri."""
    if not precedenti:
        return ""
    return ("# Fatti già fissati in questo capitolo (orari, luoghi, oggetti, porte, scadenze): "
            "restano veri. Un oggetto già sottratto non si sottrae di nuovo; una porta chiusa si "
            "apre solo mostrando come; una scadenza ha l'ora già detta\n"
            + _state_text(precedenti, inizio or []).split("\n", 1)[-1])


_SCENE_SYSTEM = (
    "Sei il segretario di edizione di un romanzo: tieni il registro degli eventi. Ricevi lo "
    "STATO prima di una scena (dove e quando è il protagonista, oggetti e documenti con chi li "
    "ha, porte e chiavi, scadenze con l'ora, date e stagione) e la SCENA. Registra i fatti della "
    "scena, come il testo li dice (non inventare, non completare): 'flashback' vero se è un "
    "episodio di un altro tempo o un ricordo; 'day' e 'time_start'/'time_end' come li dice il "
    "testo (vuoti se non li dice); 'place_start'/'place_end'; 'arrival': la frase del testo che "
    "mostra come il protagonista arriva qui da dove era (vuoto se il testo non lo mostra); "
    "'present' chi c'è; 'events' i fatti con l'ora; 'objects' gli oggetti e i documenti che "
    "contano (originale, copia, pagina, chiave, telefono…) con chi li ha e lo stato; 'access' "
    "porte, cancelli e chiavi; 'locked_in_at_end' vero se a fine scena il protagonista è chiuso "
    "dentro senza uscita; 'deadlines' le scadenze con l'ora; 'dates' le date o i codici che "
    "sembrano date, alla lettera; 'season' la stagione se il testo la dice; 'knowledge' chi "
    "viene a sapere qualcosa di importante in questa scena, cosa, e da chi o da quale prova "
    "('source'; vuoto se il testo non lo mostra). USA GLI STESSI ID "
    "dello stato per le stesse cose; un oggetto nuovo ha un id nuovo.\n\n"
    "In 'issues' le contraddizioni fra la scena e lo stato, o dentro la scena: un oggetto preso "
    "due volte (o non si capisce se è lo stesso), una porta chiusa attraversata senza che si veda "
    "come, uno spostamento senza causa né mezzo, orari che non tornano, una scadenza con un'ora "
    "diversa, una data che non va con la stagione, chi sa cosa. Per ognuna: 'severity' "
    "(bloccante: impossibile; importante: il lettore se ne accorge; minore), 'kind' (tempo, "
    "luogo, oggetto, accesso, scadenza, data, conoscenza), 'problem' in una riga, 'quote' la "
    "frase del testo, 'fix' la correzione minima. Se non ce ne sono, 'issues' è vuoto. Non "
    "segnalare scelte di stile né fatti storici: solo la coerenza interna del racconto."
)


def record_scene(client: Any, heading: str, text: str, precedenti: list[SceneRecord],
                 inizio: list[str] | None = None, *, plan_card: str = "",
                 model: str | None = None) -> tuple[SceneRecord, list[Issue]]:
    """I fatti di una scena e le contraddizioni con lo stato. Non solleva."""
    utente = "\n\n".join(p for p in (
        "# Lo stato prima della scena\n" + _state_text(precedenti, inizio or []),
        f"# Il piano di questa scena\n{plan_card}" if plan_card else "",
        f"# La scena\n{heading}\n\n{text}") if p)
    try:
        r = client.chat_json([{"role": "system", "content": _SCENE_SYSTEM},
                              {"role": "user", "content": utente}],
                             schema=SCENE_SCHEMA, model=model)
    except Exception as exc:  # noqa: BLE001 - un registro mancato si dice, non ferma il libro
        return SceneRecord(heading=heading, error=str(exc)[:160]), []
    if not isinstance(r, dict) or not r:
        return SceneRecord(heading=heading, error="nessuna risposta"), []
    record = record_from_dict({**r, "heading": heading})
    problemi = []
    for v in r.get("issues") or []:
        if not isinstance(v, dict) or not str(v.get("problem") or "").strip():
            continue
        gravita = str(v.get("severity") or MINOR).lower()
        problemi.append(Issue(severity=gravita if gravita in SEVERITIES else MINOR,
                              kind=str(v.get("kind") or ""), problem=str(v["problem"]).strip(),
                              quote=str(v.get("quote") or "").strip(),
                              fix=str(v.get("fix") or "").strip()))
    return record, problemi


# --------------------------------------------------------------------------- #
#  i controlli che non chiedono un modello
# --------------------------------------------------------------------------- #
_ORA = re.compile(r"\b(\d{1,2})(?:[:.h](\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?(?![\d/-])",
                  re.IGNORECASE)
_MESI = {
    1: "january gennaio janvier januar enero janeiro",
    2: "february febbraio février fevrier februar febrero fevereiro",
    3: "march marzo mars märz marz março",
    4: "april aprile avril abril",
    5: "may maggio mai mayo maio",
    6: "june giugno juin juni junio junho",
    7: "july luglio juillet juli julio julho",
    8: "august agosto août aout",
    9: "september settembre septembre septiembre setembro",
    10: "october ottobre octobre oktober octubre outubro",
    11: "november novembre noviembre novembro",
    12: "december dicembre décembre decembre dezember diciembre dezembro",
}
_STAGIONI = {
    "primavera": ("spring primavera printemps frühling fruhling", {3, 4, 5, 6}),
    "estate": ("summer estate été ete sommer verano verão verao", {6, 7, 8, 9}),
    "autunno": ("autumn fall autunno automne herbst otoño otono outono", {9, 10, 11, 12}),
    "inverno": ("winter inverno hiver invierno", {12, 1, 2, 3}),
}


def _minutes(testo: str) -> int | None:
    """L'ora di un'indicazione di tempo, in minuti dalla mezzanotte; None se non c'è un'ora."""
    for m in _ORA.finditer(testo or ""):
        ore, minuti, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if not m.group(2) and not ampm:
            continue  # un numero da solo non è un'ora
        if ore > 23 or minuti > 59:
            continue
        if ampm.startswith("p") and ore < 12:
            ore += 12
        elif ampm.startswith("a") and ore == 12:
            ore = 0
        return ore * 60 + minuti
    return None


def _month(data: str) -> int | None:
    testo = (data or "").lower()
    if m := re.search(r"\b(1\d{3}|20\d{2})-(\d{1,2})-(\d{1,2})\b", testo):
        mese = int(m.group(2))
        return mese if 1 <= mese <= 12 else None
    if m := re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](1\d{3}|20\d{2})\b", testo):
        mese = int(m.group(2))  # giorno/mese/anno: la forma europea
        return mese if 1 <= mese <= 12 else None
    for mese, nomi in _MESI.items():
        if any(re.search(rf"\b{re.escape(n)}\b", testo) for n in nomi.split()):
            return mese
    return None


def _season(testo: str) -> str:
    t = (testo or "").lower()
    for nome, (parole, _mesi) in _STAGIONI.items():
        if any(re.search(rf"\b{re.escape(p)}\b", t) for p in parole.split()):
            return nome
    return ""


def _places_meet(a: str, b: str) -> bool:
    pa = {w for w in content_words(a) if len(w) > 3}
    pb = {w for w in content_words(b) if len(w) > 3}
    return not pa or not pb or bool(pa & pb)


def check(records: list[SceneRecord], previous_place: str = "") -> list[Issue]:
    """Le contraddizioni che si vedono senza modello, scena per scena."""
    problemi: list[Issue] = []
    ultimo: SceneRecord | None = None
    ultimo_i = -1
    minuti_prima: int | None = None
    giorno_prima = ""
    sottratti: dict[str, int] = {}
    scadenze: dict[str, tuple[int, str, int | None]] = {}
    for i, r in enumerate(records):
        if r.error:
            continue
        for o in r.objects:
            if o.get("status") == "sottratto" and o.get("id"):
                if o["id"] in sottratti and sottratti[o["id"]] != i:
                    problemi.append(Issue(
                        BLOCKING, "oggetto",
                        f"«{o.get('what', o['id'])}» viene sottratto due volte (scena "
                        f"{sottratti[o['id']] + 1} e scena {i + 1})", section=i, origin="regola",
                        fix="se sono due documenti diversi, dillo e distinguili; se è lo stesso, "
                            "nella seconda scena il protagonista lo ha già"))
                else:
                    sottratti.setdefault(o["id"], i)
        for s in r.deadlines:
            quando = _minutes(s.get("when", ""))
            if s.get("id") in scadenze:
                k, testo, minuti = scadenze[s["id"]]
                if quando is not None and minuti is not None and quando != minuti:
                    problemi.append(Issue(
                        IMPORTANT, "scadenza",
                        f"«{s.get('what', s['id'])}»: alle {testo} nella scena {k + 1}, alle "
                        f"{s.get('when')} nella scena {i + 1}", section=i, origin="regola",
                        fix="un'ora sola, o due eventi distinti e detti come tali"))
            elif s.get("id"):
                scadenze[s["id"]] = (i, s.get("when", ""), quando)
        stagione = _season(r.season)
        if stagione and not r.flashback:
            for data in r.dates:
                mese = _month(data)
                if mese is not None and mese not in _STAGIONI[stagione][1]:
                    problemi.append(Issue(
                        IMPORTANT, "data",
                        f"«{data}» (mese {mese}) non va con la stagione detta ({r.season})",
                        quote=data, section=i, origin="regola",
                        fix="se è una data, accordala con la stagione; se è un codice, non "
                            "deve sembrare una data"))
        if r.flashback:
            continue
        # il presente: da dove arriva, a che ora
        da_dove = (ultimo.place_end or ultimo.place_start) if ultimo else previous_place
        if da_dove and r.place_start and not _places_meet(da_dove, r.place_start) \
                and not r.arrival.strip():
            chiuso = ultimo is not None and ultimo.locked_in_at_end
            problemi.append(Issue(
                BLOCKING if chiuso else IMPORTANT, "luogo",
                (f"alla fine della scena {ultimo_i + 1} il protagonista è chiuso dentro "
                 f"({da_dove}) e qui è a «{r.place_start}»: la fuga non è mostrata") if chiuso else
                (f"il protagonista era a «{da_dove}» e qui è a «{r.place_start}» senza che si veda "
                 "come ci è arrivato" + ("" if ultimo else " (dal capitolo precedente)")),
                section=i, origin="regola",
                fix="mostra l'uscita o lo spostamento: causa, mezzo, tempo"))
        minuti = _minutes(r.time_start) if r.time_start else None
        if minuti is not None and minuti_prima is not None and \
                (not r.day or not giorno_prima or r.day.strip().lower() ==
                 giorno_prima.strip().lower()):
            notte = minuti_prima >= 17 * 60 and minuti <= 7 * 60  # dopo la mezzanotte
            if minuti < minuti_prima and not notte:
                problemi.append(Issue(
                    IMPORTANT, "tempo",
                    f"la scena {i + 1} comincia alle {r.time_start}, prima della fine della scena "
                    f"{ultimo_i + 1} ({ultimo.time_end or ultimo.time_start if ultimo else '?'})",
                    section=i, origin="regola", fix="metti gli orari in ordine"))
        fine = _minutes(r.time_end) if r.time_end else None
        if fine is not None or minuti is not None:
            minuti_prima = fine if fine is not None else minuti
        if r.day:
            giorno_prima = r.day
        ultimo, ultimo_i = r, i
    return problemi


def check_book(chapters: list[tuple[int, list[SceneRecord]]]) -> list[tuple[int, Issue]]:
    """Le contraddizioni che si vedono senza modello **fra capitoli**: (capitolo, problema).

    ``chapters``: (numero, registro delle scene) dei capitoli di cui c'è la cronologia, in
    qualsiasi ordine. Guarda quello che :func:`check` vede dentro un capitolo solo, ma a cavallo:
    una scadenza che in due capitoli ha due orari, uno stesso oggetto sottratto in due capitoli.
    Il passaggio di luogo da un capitolo all'altro lo guarda già :func:`check`, con il luogo in
    cui il capitolo prima lascia il protagonista.
    """
    problemi: list[tuple[int, Issue]] = []
    scadenze: dict[str, tuple[int, str, int | None]] = {}
    sottratti: dict[str, int] = {}
    for numero, records in sorted(chapters, key=lambda x: x[0]):
        visti_qui: set[str] = set()
        for k, r in enumerate(records):
            if r.error:
                continue
            for s in r.deadlines:
                ident = s.get("id") or ""
                if not ident:
                    continue
                quando = _minutes(s.get("when", ""))
                if ident in scadenze and scadenze[ident][0] != numero:
                    capitolo, testo, minuti = scadenze[ident]
                    if quando is not None and minuti is not None and quando != minuti:
                        problemi.append((numero, Issue(
                            IMPORTANT, "scadenza",
                            f"«{s.get('what', ident)}»: alle {testo} nel capitolo {capitolo}, "
                            f"alle {s.get('when')} qui", section=k, origin="regola",
                            fix="un'ora sola, o due eventi distinti e detti come tali")))
                elif quando is not None:
                    scadenze[ident] = (numero, s.get("when", ""), quando)
            for o in r.objects:
                ident = o.get("id") or ""
                if o.get("status") != "sottratto" or not ident or ident in visti_qui:
                    continue
                visti_qui.add(ident)
                if ident in sottratti and sottratti[ident] != numero:
                    problemi.append((numero, Issue(
                        IMPORTANT, "oggetto",
                        f"«{o.get('what', ident)}» viene sottratto qui e già nel capitolo "
                        f"{sottratti[ident]}", section=k, origin="regola",
                        fix="se sono due oggetti diversi, distinguili; se è lo stesso, il "
                            "protagonista lo ha già")))
                else:
                    sottratti.setdefault(ident, numero)
    return problemi


# --------------------------------------------------------------------------- #
#  il capitolo intero: i fili aperti, gli eventi dei capitoli dopo
# --------------------------------------------------------------------------- #
_CHAPTER_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "threads": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"thread": _T,
                           "status": {"type": "string",
                                      "enum": ["risolto", "rimandato", "ignorato"]},
                           "where": _T},
            "required": ["thread", "status", "where"]}},
        "anticipations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"event": _T, "chapter": _T, "quote": _T, "section": {"type": "integer"}},
            "required": ["event", "chapter", "quote", "section"]}},
    },
    "required": ["threads", "anticipations"],
}


def threads_and_anticipations(client: Any, sections: list[tuple[str, str]], pack: Any, *,
                              model: str | None = None) -> list[Issue]:
    """Cosa ne fa il capitolo dei fili aperti del precedente, e se anticipa i successivi."""
    fili = list(getattr(pack, "open_threads", []) or [])
    prossima = getattr(getattr(getattr(pack, "previous", None), "end_state", None),
                       "next_action", "")
    if prossima:
        fili.append(f"la prossima azione: {prossima}")
    dopo = list(getattr(pack, "following", []) or [])
    if not fili and not dopo:
        return []
    sistema = (
        "Sei il segretario di edizione di un romanzo. Per ogni filo aperto dal capitolo "
        "precedente (un appuntamento, una persona, una promessa, la prossima azione) di' se "
        "questo capitolo lo 'risolto' (lo mostra), lo 'rimandato' (il lettore sa che resta "
        "aperto e perché) o lo 'ignorato' (non ne parla affatto, e il lettore se lo chiederà); "
        "in 'where' la sezione o la frase. In 'anticipations' gli eventi che la struttura "
        "assegna ai capitoli successivi e che questo capitolo mette già in scena o rivela, con "
        "la citazione e il numero della sezione (vuoto se non ce ne sono). Non giudicare lo "
        "stile."
    )
    corpo = "\n\n".join(f"## [{i + 1}] {h.lstrip('# ')}\n{t}" for i, (h, t) in enumerate(sections))
    utente = "\n\n".join(p for p in (
        ("# Fili aperti dal capitolo precedente\n" + "\n".join(f"- {f}" for f in fili))
        if fili else "",
        ("# I capitoli dopo, come li vuole la struttura\n" + "\n".join(dopo)) if dopo else "",
        f"# Il capitolo, a sezioni numerate\n{corpo}") if p)
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=_CHAPTER_SCHEMA, model=model)
    except Exception:  # noqa: BLE001 - chi chiama lo annota
        return []
    problemi: list[Issue] = []
    for f in (r.get("threads") or []) if isinstance(r, dict) else []:
        if isinstance(f, dict) and f.get("status") == "ignorato" and f.get("thread"):
            problemi.append(Issue(IMPORTANT, "filo aperto",
                                  f"il filo «{f['thread']}» del capitolo precedente sparisce in "
                                  "silenzio", section=0,
                                  fix="mostra cosa ne è (risolto, rimandato o chiuso), anche in "
                                      "poche righe, dove il capitolo riparte"))
    for a in (r.get("anticipations") or []) if isinstance(r, dict) else []:
        if not isinstance(a, dict) or not a.get("event"):
            continue
        try:
            k = int(a.get("section") or 0) - 1
        except (TypeError, ValueError):
            k = -1
        problemi.append(Issue(IMPORTANT, "anticipazione",
                              f"anticipa «{a['event']}» ({a.get('chapter', 'un capitolo dopo')})",
                              quote=str(a.get("quote") or ""),
                              section=k if 0 <= k < len(sections) else -1,
                              fix="lascia la domanda aperta: l'evento appartiene al capitolo dopo"))
    return problemi


# --------------------------------------------------------------------------- #
#  tutto il capitolo
# --------------------------------------------------------------------------- #
def build(client: Any, sections: list[tuple[str, str]], pack: Any = None, piano: Any = None, *,
          model: str | None = None, scene_for: Any = None) -> Timeline:
    """Il registro degli eventi del capitolo e i suoi problemi.

    ``sections``: (titolo, testo) per sezione, nell'ordine. ``scene_for(i)`` dà la scena del piano
    della sezione ``i`` (per la sua scheda), se c'è.
    """
    inizio = _start_lines(pack)
    cronologia = Timeline()
    for i, (titolo, testo) in enumerate(sections):
        if not testo.strip():
            cronologia.records.append(SceneRecord(heading=titolo, error="vuota"))
            continue
        scena = scene_for(i) if scene_for else None
        record, problemi = record_scene(client, titolo, testo, cronologia.records, inizio,
                                        plan_card=scena.card() if scena is not None else "",
                                        model=model)
        cronologia.records.append(record)
        for p in problemi:
            p.section = i
        cronologia.issues += problemi
    precedente = ""
    if pack is not None and getattr(pack, "previous", None) is not None:
        precedente = pack.previous.end_state.place
    cronologia.issues += check(cronologia.records, precedente)
    if pack is not None:
        cronologia.issues += threads_and_anticipations(client, sections, pack, model=model)
    cronologia.issues = _dedup(cronologia.issues)
    return cronologia


def _start_lines(pack: Any) -> list[str]:
    if pack is None or getattr(pack, "previous", None) is None:
        return []
    righe = list(pack.previous.end_state.lines())
    if getattr(pack, "open_threads", None):
        righe.append("- Fili aperti: " + "; ".join(pack.open_threads))
    return righe


def _dedup(problemi: list[Issue]) -> list[Issue]:
    visti: set[tuple[int, str]] = set()
    fuori = []
    for p in sorted(problemi, key=lambda x: SEVERITIES.index(x.severity)
                    if x.severity in SEVERITIES else 9):
        chiave = (p.section, " ".join(sorted(content_words(p.problem)))[:120])
        if chiave in visti:
            continue
        visti.add(chiave)
        fuori.append(p)
    return fuori


# --------------------------------------------------------------------------- #
#  il piano, prima di scrivere
# --------------------------------------------------------------------------- #
_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"issues": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"scene": _T, "problem": _T,
                       "severity": {"type": "string",
                                    "enum": ["blocker", "major", "minor", "preference"]}},
        "required": ["scene", "problem", "severity"]}}},
    "required": ["issues"],
}


def check_plan(client: Any, piano: Any, pack: Any, *, model: str | None = None) -> list[str]:
    """La continuità del piano delle scene, prima di scrivere: i difetti, in righe brevi."""
    if client is None or piano is None or not getattr(piano, "scenes", None):
        return []
    sistema = (
        "Sei il segretario di edizione di un romanzo e controlli il PIANO delle scene di un "
        "capitolo prima che si scriva. Cerca solo i problemi di sequenza: il capitolo riparte "
        "da dove il precedente ha lasciato il protagonista, o il passaggio è una catena causale "
        "(indizio → decisione del protagonista → spostamento → nuovo obiettivo) e non un "
        "trasporto subito? I fili aperti del capitolo precedente (appuntamenti, persone, "
        "promesse) hanno un seguito? Orari e giorni sono in ordine e possibili? Una scadenza ha "
        "un'ora sola? Uno stesso oggetto viene preso o sottratto due volte? Se il protagonista "
        "resta chiuso, la scena dopo mostra come esce? Date e stagione vanno d'accordo? Il "
        "capitolo anticipa eventi dei capitoli successivi? Più scene fanno la stessa cosa? "
        f"Al massimo {MAX_PLAN_ISSUES} problemi, ciascuno con la scena ('scene'), il problema "
        "in una riga concreta e la gravità ('severity'): 'blocker' se rende impossibile una "
        "stesura coerente (cronologia impossibile, un conteggio che non torna, la continuità "
        "col capitolo precedente rotta, il canone violato), 'major' se indebolisce causalità, "
        "comprensione o suspense, 'minor' se è locale, 'preference' se è gusto. Se il piano "
        "regge, 'issues' è vuoto. Non giudicare lo stile, non chiedere di verificare fatti "
        "storici o invenzioni."
    )
    righe = []
    if piano.bridge_text():
        righe.append(f"Ponte dal capitolo prima: {piano.bridge_text()}")
    for t in piano.threads:
        righe.append(f"Filo «{t.get('thread', '')}»: {t.get('handling', '')}")
    righe += [f"Partenza: {piano.start}", f"Arrivo: {piano.end}", ""]
    for s in piano.scenes:
        righe.append(f"Scena {s.id} ({'episodio' if s.kind != 'presente' else 'presente'}): "
                     f"{s.place}; {s.time}; personaggi: {s.characters}; obiettivo: {s.goal}; "
                     f"ostacolo: {s.obstacle}; indizio: {s.clue}; decisione: {s.decision}; "
                     f"conseguenza: {s.consequence}; ponte: {s.bridge}")
    utente = "\n\n".join(p for p in (
        pack.start_block() if pack is not None else "",
        pack.outline_block() if pack is not None else "",
        "# Il piano\n" + "\n".join(righe)) if p)
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=_PLAN_SCHEMA, model=model)
    except Exception:  # noqa: BLE001 - senza controllo restano i difetti deterministici
        return []
    fuori = []
    for v in ((r.get("issues") or []) if isinstance(r, dict) else [])[:MAX_PLAN_ISSUES]:
        if isinstance(v, dict) and str(v.get("problem") or "").strip():
            from bookwriter.forge import concepts as cn

            dove = str(v.get("scene") or "").strip()
            gravita = cn.normalise("gravita", str(v.get("severity") or "")) or "major"
            fuori.append(f"[{gravita}] continuità del piano{', scena ' + dove if dove else ''}: "
                         f"{str(v['problem']).strip()}")
    return fuori
