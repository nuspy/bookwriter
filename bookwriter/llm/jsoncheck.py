"""Controlli su ciò che il modello scrive: un JSON conforme allo schema, senza ripetizioni.

Il server non vincola l'uscita (alcuni lo dichiarano e poi la validano da sé, ritentando in
silenzio per minuti): la forma si controlla **qui**, su quello che arriva, e si ripara con una
richiesta mirata. Tre cose, tutte sul risultato e mai sul ragionamento:

* :func:`validate` — il JSON rispetta lo schema? Il sottoinsieme che serve ai compiti del
  programma (tipi, proprietà richieste, nessuna proprietà in più, elementi degli elenchi);
* :func:`clean_repetitions` — un elenco con voci uguali si deduplica; un elenco o un testo
  **dominati** dalla ripetizione (la stessa frase che torna, le voci distinte in minoranza) sono un
  risultato degenerato e si segnalano;
* :func:`repeated_text` — prosa con paragrafi o frasi ripetuti identici (per i capitoli).
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Un elenco con almeno tante voci, di cui le distinte sono meno della metà, è degenerato.
_MIN_LIST = 4
#: Una frase (o un paragrafo) più corta di così può ripetersi senza che sia un difetto («Sì.»).
_MIN_SENTENCE = 40
#: Quante volte la stessa frase può tornare in un campo prima che sia ripetizione.
_MAX_SAME_SENTENCE = 2


class Problem(str):
    """Un problema di ripetizione: il testo in parole, e dove sta.

    ``path`` è il campo, ``repeated`` la voce che si ripete (una volta sola) e ``count`` quante
    volte compare: è quanto serve a una riparazione mirata, che non rimanda mai il testo intero.
    """

    path: str
    repeated: str
    count: int

    def __new__(cls, message: str, path: str = "", repeated: str = "", count: int = 0):
        obj = super().__new__(cls, message)
        obj.path, obj.repeated, obj.count = path, repeated, count
        return obj


def value_at(data: Any, path: str) -> Any:
    """Il valore a un percorso come ``$.items[1].name``; ``None`` se non c'è."""
    corrente = data
    for parte in re.findall(r"\.([A-Za-z0-9_]+)|\[(\d+)\]", path):
        try:
            corrente = corrente[parte[0]] if parte[0] else corrente[int(parte[1])]
        except (KeyError, IndexError, TypeError):
            return None
    return corrente


def snippet(value: Any, limit: int = 400) -> str:
    """Il punto di un oggetto JSON, in forma compatta e corta: quello che si mostra al modello."""
    testo = json.dumps(value, ensure_ascii=False)
    return testo if len(testo) <= limit else testo[:limit - 1] + "…"


def normalize(text: str) -> str:
    """Il testo confrontabile: minuscolo, spazi singoli, senza punteggiatura ai bordi."""
    return re.sub(r"\s+", " ", str(text).lower()).strip(" \t\n.,;:!?-–—*_\"'«»“”()[]")


def _type_ok(value: Any, tipo: str) -> bool:
    if tipo == "object":
        return isinstance(value, dict)
    if tipo == "array":
        return isinstance(value, list)
    if tipo == "string":
        return isinstance(value, str)
    if tipo == "boolean":
        return isinstance(value, bool)
    if tipo == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if tipo == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if tipo == "null":
        return value is None
    return True


def validate(data: Any, schema: dict[str, Any], path: str = "$") -> str | None:
    """Il primo errore, in parole e col suo percorso, o ``None`` se il dato rispetta lo schema."""
    tipo = schema.get("type")
    if tipo is not None:
        tipi = tipo if isinstance(tipo, list) else [tipo]
        if not any(_type_ok(data, t) for t in tipi):
            return f"{path}: doveva essere di tipo {'/'.join(tipi)}"
    if "enum" in schema and data not in schema["enum"]:
        return f"{path}: valore non ammesso ({data!r})"
    if isinstance(data, dict):
        proprieta = schema.get("properties") or {}
        for chiave in schema.get("required") or []:
            if chiave not in data:
                return f"{path}: manca la proprietà «{chiave}»"
        if schema.get("additionalProperties") is False:
            for chiave in data:
                if chiave not in proprieta:
                    return f"{path}: proprietà non prevista «{chiave}»"
        for chiave, sotto in proprieta.items():
            if chiave in data:
                errore = validate(data[chiave], sotto, f"{path}.{chiave}")
                if errore:
                    return errore
    elif isinstance(data, list) and isinstance(schema.get("items"), dict):
        for i, voce in enumerate(data):
            errore = validate(voce, schema["items"], f"{path}[{i}]")
            if errore:
                return errore
    return None


def schema_at(schema: dict[str, Any], path: str) -> dict[str, Any]:
    """Il pezzo di schema a un percorso come ``$.competitors[1].year`` ({} se non c'è)."""
    corrente = schema
    for nome, indice in re.findall(r"\.([A-Za-z0-9_]+)|\[(\d+)\]", path):
        if not isinstance(corrente, dict):
            return {}
        if nome:
            corrente = (corrente.get("properties") or {}).get(nome, {})
        else:
            corrente = corrente.get("items", {})
    return corrente if isinstance(corrente, dict) else {}


def _readable(path: str) -> str:
    """``$.competitors[1]`` -> ``/competitors/1``: un percorso che un modello legge senza sbagliare."""
    pezzi = [a or b for a, b in re.findall(r"\.([A-Za-z0-9_]+)|\[(\d+)\]", path)]
    return "/" + "/".join(pezzi)


def empty_for(schema: dict[str, Any]) -> str:
    """Il valore «non noto» di un campo, in parole, secondo il suo tipo."""
    tipo = schema.get("type")
    tipo = tipo[0] if isinstance(tipo, list) and tipo else tipo
    return {"string": 'una stringa vuota ("")', "array": "un elenco vuoto ([])",
            "integer": "0", "number": "0", "boolean": "false",
            "object": "un oggetto vuoto ({})"}.get(tipo, 'una stringa vuota ("")')


_MISSING = re.compile(r"^(?P<path>\S+): manca la proprietà «(?P<key>[^»]+)»$")
_EXTRA = re.compile(r"^(?P<path>\S+): proprietà non prevista «(?P<key>[^»]+)»$")
_TYPE = re.compile(r"^(?P<path>\S+): doveva essere di tipo (?P<tipo>\S+)$")
_ENUM = re.compile(r"^(?P<path>\S+): valore non ammesso")


def fix_instruction(error: str, schema: dict[str, Any]) -> str:
    """L'istruzione mirata per un JSON **valido** che viola solo lo schema.

    Dice cosa cambiare e dove, e che il resto resta com'è. Per una proprietà mancante dice anche
    cosa scrivere se il valore non è noto: senza, il modello la omette ancora. Niente conteggi.
    """
    if m := _MISSING.match(error):
        dentro = schema_at(schema, m["path"])
        sotto = (dentro.get("properties") or {}).get(m["key"], {})
        return (f"Aggiungi la chiave «{m['key']}» in {_readable(m['path'])}: se il valore non è "
                f"noto scrivi {empty_for(sotto)}, ma la chiave deve esserci.")
    if m := _EXTRA.match(error):
        return f"Togli la chiave «{m['key']}» da {_readable(m['path'])}: lo schema non la prevede."
    if m := _TYPE.match(error):
        return f"Il valore in {_readable(m['path'])} deve essere di tipo {m['tipo']}."
    if m := _ENUM.match(error):
        ammessi = schema_at(schema, m["path"]).get("enum") or []
        return (f"Il valore in {_readable(m['path'])} deve essere uno di questi: "
                + ", ".join(json.dumps(v, ensure_ascii=False) for v in ammessi) + ".")
    return f"Correggi questo, e solo questo: {error}."


def _key(voce: Any) -> str:
    if isinstance(voce, (dict, list)):
        return normalize(json.dumps(voce, sort_keys=True, ensure_ascii=False))
    return normalize(str(voce))


def _sentences(text: str) -> list[str]:
    return [normalize(s) for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) >= _MIN_SENTENCE]


def clean_repetitions(data: Any, path: str = "$") -> tuple[Any, list[str]]:
    """Il dato senza voci doppie, e i problemi di ripetizione **dominante** (in parole).

    Le voci uguali di un elenco si tolgono in silenzio: è un difetto da poco. Un elenco in cui le
    voci distinte sono una minoranza, o un testo in cui la stessa frase torna più volte, no: il
    modello si è avvitato, e il resto del campo non è affidabile. Questo si ritorna come problema.
    """
    problemi: list[str] = []
    if isinstance(data, dict):
        pulito = {}
        for chiave, valore in data.items():
            pulito[chiave], p = clean_repetitions(valore, f"{path}.{chiave}")
            problemi += p
        return pulito, problemi
    if isinstance(data, list):
        elementi = []
        for i, voce in enumerate(data):
            v, p = clean_repetitions(voce, f"{path}[{i}]")
            elementi.append(v)
            problemi += p
        visti: set[str] = set()
        distinti = []
        for voce in elementi:
            chiave = _key(voce)
            if chiave and chiave in visti:
                continue
            visti.add(chiave)
            distinti.append(voce)
        if len(elementi) >= _MIN_LIST and len(distinti) * 2 < len(elementi):
            conta: dict[str, int] = {}
            for voce in elementi:
                conta[_key(voce)] = conta.get(_key(voce), 0) + 1
            chiave, quante = max(conta.items(), key=lambda kv: kv[1])
            esempio = next(v for v in elementi if _key(v) == chiave)
            problemi.append(Problem(
                f"l'elenco «{path}» ha {len(elementi)} voci ma solo {len(distinti)} diverse: "
                "la stessa voce ripetuta", path, snippet(esempio, 200), quante))
        return distinti, problemi
    if isinstance(data, str):
        ripetute = _repeated_sentences(data)
        if ripetute:
            problemi.append(Problem(
                f"il campo «{path}» ripete la stessa frase {ripetute[1]} volte",
                path, ripetute[0][:200], ripetute[1]))
    return data, problemi


def _repeated_sentences(text: str) -> tuple[str, int] | None:
    conta: dict[str, int] = {}
    for frase in _sentences(text):
        conta[frase] = conta.get(frase, 0) + 1
    peggio = max(conta.items(), key=lambda kv: kv[1], default=None)
    if peggio and peggio[1] > _MAX_SAME_SENTENCE:
        return peggio
    return None


def repeated_text(text: str) -> list[str]:
    """I paragrafi o le frasi ripetuti identici in una prosa (capitolo o sezione).

    Un paragrafo lungo che compare due volte, o la stessa frase lunga tre: un testo scritto bene
    non lo fa, e di solito è un modello che si è avvitato. Ritorna la descrizione di ciascuno.
    """
    trovati: list[str] = []
    paragrafi: dict[str, int] = {}
    for blocco in re.split(r"\n\s*\n", text or ""):
        if blocco.lstrip().startswith("#"):
            continue
        chiave = normalize(blocco)
        if len(chiave) >= _MIN_SENTENCE:
            paragrafi[chiave] = paragrafi.get(chiave, 0) + 1
    for chiave, n in paragrafi.items():
        if n >= 2:
            trovati.append(f"paragrafo ripetuto {n} volte: «{chiave[:60]}…»")
    frase = _repeated_sentences("\n".join(b for b in re.split(r"\n\s*\n", text or "")
                                          if not b.lstrip().startswith("#")))
    if frase:
        trovati.append(f"frase ripetuta {frase[1]} volte: «{frase[0][:60]}…»")
    return trovati


def dedupe_paragraphs(text: str) -> str:
    """Toglie i paragrafi identici già comparsi (l'ultima risorsa, se la riscrittura ripete ancora)."""
    visti: set[str] = set()
    fuori = []
    for blocco in re.split(r"\n\s*\n", text or ""):
        chiave = normalize(blocco)
        if len(chiave) >= _MIN_SENTENCE and not blocco.lstrip().startswith("#"):
            if chiave in visti:
                continue
            visti.add(chiave)
        fuori.append(blocco)
    return "\n\n".join(fuori)


# --------------------------------------------------------------------------- #
#  un JSON rotto o troncato: dove, e che cosa si ripete — senza citare il testo
# --------------------------------------------------------------------------- #
class _Stop(Exception):
    """Il prefisso valido finisce qui: posizione e contenitore aperto (tipo, percorso, contenuto)."""

    def __init__(self, pos: int) -> None:
        super().__init__(pos)
        self.pos = pos
        self.container: tuple[str, str, list] | None = None
        self.root_keys: list[str] = []


_NUMBER = re.compile(r"-?\d+(\.\d+)?([eE][+-]?\d+)?")
_LITERALS = (("true", True), ("false", False), ("null", None))


def _skip(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def _p_string(s: str, i: int) -> tuple[str, int]:
    j = i + 1
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == '"':
            try:
                return json.loads(s[i:j + 1]), j + 1
            except ValueError:
                return s[i + 1:j], j + 1
        j += 1
    raise _Stop(len(s))


def _p_value(s: str, i: int, path: str) -> tuple[Any, int]:
    i = _skip(s, i)
    if i >= len(s):
        raise _Stop(i)
    c = s[i]
    if c == "{":
        return _p_object(s, i, path)
    if c == "[":
        return _p_array(s, i, path)
    if c == '"':
        return _p_string(s, i)
    if (m := _NUMBER.match(s, i)):
        return float(m.group()) if any(x in m.group() for x in ".eE") else int(m.group()), m.end()
    for nome, valore in _LITERALS:
        if s.startswith(nome, i):
            return valore, i + len(nome)
        if nome.startswith(s[i:]):
            raise _Stop(len(s))
    raise _Stop(i)


def _p_array(s: str, i: int, path: str) -> tuple[list, int]:
    items: list = []
    i += 1
    try:
        i = _skip(s, i)
        if i < len(s) and s[i] == "]":
            return items, i + 1
        while True:
            v, i = _p_value(s, i, f"{path}[{len(items)}]")
            items.append(v)
            i = _skip(s, i)
            if i >= len(s):
                raise _Stop(i)
            if s[i] == ",":
                i += 1
                continue
            if s[i] == "]":
                return items, i + 1
            raise _Stop(i)
    except _Stop as e:
        if e.container is None:
            e.container = ("array", path, items)
        raise


def _p_object(s: str, i: int, path: str) -> tuple[dict, int]:
    out: dict = {}
    i += 1
    try:
        i = _skip(s, i)
        if i < len(s) and s[i] == "}":
            return out, i + 1
        while True:
            i = _skip(s, i)
            if i >= len(s) or s[i] != '"':
                raise _Stop(i)
            chiave, i = _p_string(s, i)
            i = _skip(s, i)
            if i >= len(s) or s[i] != ":":
                raise _Stop(i)
            valore, i = _p_value(s, i + 1, f"{path}.{chiave}")
            out[chiave] = valore
            i = _skip(s, i)
            if i >= len(s):
                raise _Stop(i)
            if s[i] == ",":
                i += 1
                continue
            if s[i] == "}":
                return out, i + 1
            raise _Stop(i)
    except _Stop as e:
        if e.container is None:
            e.container = ("object", path, list(out))
        if path == "$":
            e.root_keys = list(out)
        raise


_SYNTAX_WORDS = (
    ("Unterminated string", "una stringa non è chiusa"),
    ("Expecting ',' delimiter", "manca una virgola o una chiusura"),
    ("Expecting value", "manca un valore"),
    ("Expecting property name", "manca il nome di una proprietà"),
    ("Expecting ':' delimiter", "mancano i due punti dopo il nome di una proprietà"),
    ("Extra data", "c'è del testo dopo la fine del JSON"),
)


def _line_col(s: str, pos: int) -> tuple[int, int]:
    return s.count("\n", 0, pos) + 1, pos - (s.rfind("\n", 0, pos) + 1) + 1


class Broken:
    """Che cosa si sa di un JSON non valido, senza citarlo: l'errore in parole e dove sta."""

    def __init__(self, message: str, hint: str) -> None:
        self.message, self.hint = message, hint


def analyze_broken(text: str) -> Broken:
    """Analisi tollerante di un JSON non valido o troncato.

    Del **prefisso valido** si ricava il percorso del campo aperto al punto del guasto (per
    esempio ``$.implicit_syllabus``), le voci complete già presenti in quell'array e se una si
    ripete. Il messaggio per la riparazione dice PRIMA l'errore (in parole, con riga e colonna,
    senza citare il testo), POI il campo, la voce ripetuta UNA volta col conteggio, e i nomi delle
    chiavi già complete. Mai una citazione che contenga la voce ripetuta più di una volta; se non
    si vede nessuna ripetizione, solo un estratto brevissimo attorno al guasto.
    """
    inizio = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if inizio == -1:
        return Broken("non c'è nessun oggetto JSON nella risposta",
                      "Estratto: " + " ".join(text.split())[:120])
    s = text[inizio:]
    try:
        _p_value(s, 0, "$")
        stop = None
    except _Stop as e:
        stop = e
    try:
        json.loads(s)
        parole, pos = "il testo non è un JSON completo", len(s)
    except json.JSONDecodeError as exc:
        parole = next((w for k, w in _SYNTAX_WORDS if exc.msg.startswith(k)), exc.msg)
        pos = exc.pos
    if stop is not None and stop.pos >= len(s.rstrip()) - 1:
        parole = "il testo termina senza chiudere il JSON (risposta troncata)"
        pos = len(s)
    riga, colonna = _line_col(s, min(pos, len(s)))
    messaggio = f"{parole} (riga {riga}, colonna {colonna})"
    parti: list[str] = []
    ripetizione = False
    if stop is not None and stop.container is not None:
        tipo, percorso, contenuto = stop.container
        if tipo == "array" and contenuto:
            conta: dict[str, list[int]] = {}
            for n, voce in enumerate(contenuto):
                conta.setdefault(_key(voce), []).append(n)
            _chiave, posizioni = max(conta.items(), key=lambda kv: len(kv[1]))
            if len(posizioni) >= 3 and len(posizioni) * 2 >= len(contenuto):
                ripetizione = True
                esempio = snippet(contenuto[posizioni[0]], 200)
                parti.append(
                    f"Il guasto sta nel campo {percorso}: la voce {esempio} compare "
                    f"{len(posizioni)} volte (voci {posizioni[0] + 1}–{posizioni[-1] + 1} su "
                    f"{len(contenuto)} già scritte).")
            else:
                parti.append(f"Il guasto sta nel campo {percorso} ({len(contenuto)} voci già complete).")
        else:
            parti.append(f"Il guasto sta nel campo {percorso}.")
    if stop is not None and stop.root_keys:
        parti.append("Chiavi già complete: " + ", ".join(stop.root_keys) + ".")
    if not ripetizione:
        da = max(0, pos - 60)
        parti.append("Estratto: …" + " ".join(s[da:pos + 60].split())[:120] + "…")
    return Broken(messaggio, "\n".join(parti))
