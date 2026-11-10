"""Il quaderno del libro: quello che è emerso dalla conversazione con l'autore, in ordine.

La conversazione è libera — l'autore racconta, l'assistente domanda, propone, discute — ma quello
che ne esce deve stare da qualche parte in una forma che le fasi successive possano leggere: la
struttura la progetta sulla trama e sulla catena degli avvenimenti, la stesura scrive ogni
capitolo sapendo chi sono i personaggi, dove si muovono e dove deve arrivare il libro.

Il quaderno è quel posto. Lo tiene aggiornato l'assistente dopo ogni scambio, e l'autore lo vede
crescere accanto alla conversazione: è lì che si accorge se qualcosa è stato capito male.

Le decisioni di stampa — misura, margini, copertina, rilegatura, canale — qui non ci sono e non
ci devono entrare: si prendono a mano, nella fase di produzione. Nemmeno lo stile si decide qui:
lo sceglie l'autore nella tendina; il quaderno ne raccoglie solo le precisazioni.
"""

from __future__ import annotations

import re
from typing import Any

from bookwriter.config import SUPPORTED_LANGUAGES, language_label, language_tag

TEXT, LIST, PEOPLE, PLACES = "text", "list", "people", "places"

#: Le voci del quaderno, nell'ordine in cui si leggono: (chiave, titolo, tipo, cosa contiene).
#: La descrizione va anche al modello che lo aggiorna: dev'essere la stessa cosa per tutti e due.
SECTIONS: list[tuple[str, str, str, str]] = [
    ("title", "Titolo di lavoro", TEXT, "il titolo, anche provvisorio"),
    ("form", "Genere e forma", TEXT,
     "romanzo, giallo, fantasy, racconti, saggio, manuale, memoir… con le sue particolarità"),
    ("language", "Lingua del libro", TEXT,
     "codice ISO 639-1 della lingua in cui va scritto il libro (it, en, fr…)"),
    ("premise", "Il libro in poche righe", TEXT,
     "la premessa: di cosa parla e cosa lo rende questo libro"),
    ("author_goal", "Cosa vuole ottenere l'autore", TEXT,
     "lo scopo dell'autore e come il libro ci arriva"),
    ("messages", "Messaggi e temi", LIST, "cosa il libro vuole dire, anche senza dirlo apertamente"),
    ("reader_effect", "L'effetto sul lettore", TEXT,
     "cosa deve provare, pensare o ricordarsi chi lo legge, durante e dopo"),
    ("audience", "Il lettore", TEXT, "chi è il lettore: età, esperienza, cosa cerca"),
    ("adaptation", "Come si adegua il libro al lettore", TEXT,
     "conseguenze sul linguaggio, sul registro, sulla complessità e sulla lunghezza dei capitoli"),
    ("length", "Lunghezza", TEXT, "lunghezza del libro e dei capitoli, se se n'è parlato"),
    ("plot", "Trama", TEXT,
     "lo sviluppo complessivo; per un libro non narrativo, la linea argomentativa"),
    ("events", "La catena degli avvenimenti", LIST,
     "gli avvenimenti o i passaggi logici, nell'ordine in cui il lettore li incontra"),
    ("causality", "Come avvengono e si legano i fatti", LIST,
     "i nessi causali: perché succede quel che succede, cosa lo rende credibile o inevitabile"),
    ("psychology", "Meccanismi psicologici e narrativi", LIST,
     ("motivazioni, paure, conflitti interiori, come cambiano i personaggi; tensione, "
      "rivelazioni, punto di vista, ritmo")),
    ("key_scenes", "Scene e passaggi chiave", LIST,
     "le scene, i paragrafi o gli avvenimenti che l'autore vuole assolutamente nel libro"),
    ("ending", "Il finale", TEXT, "come finisce il libro, e perché così"),
    ("characters", "Personaggi", PEOPLE,
     "le persone del libro (o, in un saggio, le persone reali e i casi)"),
    ("setting", "Epoca e mondo", TEXT, "quando e dove si svolge, le regole del mondo"),
    ("places", "Luoghi e ambienti", PLACES, "i luoghi e come sono"),
    ("style_notes", "Precisazioni sullo stile", TEXT,
     "solo precisazioni o suggerimenti sullo stile scelto nell'interfaccia"),
    ("avoid", "Da evitare", LIST, "argomenti, toni, parole, soluzioni da non usare"),
    ("changes", "Modifiche da fare", LIST,
     "solo quando si aggiorna un libro già scritto: ogni cambiamento deciso con l'autore, "
     "preciso (cosa diventa cosa: «l'evento all'Empire State Building si sposta sulla Torre di "
     "Pisa»), una riga ciascuno"),
    ("agreed", "Deciso insieme", LIST,
     "le proposte discusse e accettate dall'autore, una per riga"),
    ("open_questions", "Ancora da decidere", LIST,
     "le questioni aperte e le proposte in attesa di risposta"),
    ("assistant_decisions", "Deciso dall'assistente", LIST,
     "quello che l'assistente ha deciso da sé quando l'autore gli ha lasciato il resto"),
]

KEYS = [k for k, *_ in SECTIONS]
KIND = {k: tipo for k, _t, tipo, _d in SECTIONS}
LABEL = {k: titolo for k, titolo, _t, _d in SECTIONS}

#: Le voci che dicono se il libro è stato capito: sono quelle su cui l'assistente torna finché
#: restano vuote. Le altre si riempiono se il libro le richiede.
CORE = ["premise", "author_goal", "reader_effect", "audience", "plot", "events", "causality",
        "psychology", "key_scenes", "ending", "characters", "places"]

#: Le liste che si possono svuotare: una questione risolta esce dall'elenco, ed è giusto così.
CLEARABLE = {"open_questions"}

_PERSON_FIELDS: list[tuple[str, str]] = [
    ("name", "nome"), ("role", "ruolo nella storia"), ("appearance", "aspetto fisico"),
    ("mind", "psicologia e carattere"), ("culture", "cultura, origini, storia personale"),
    ("arc", "come cambia nel corso del libro"), ("notes", "relazioni, voce, altro"),
]
_PLACE_FIELDS: list[tuple[str, str]] = [("name", "nome"), ("description", "com'è")]


# --------------------------------------------------------------------------- #
#  lo schema
# --------------------------------------------------------------------------- #
def _object(fields: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {k: {"type": "string", "description": d} for k, d in fields},
        "required": [k for k, _ in fields],
    }


def schema(*, with_removed: bool = True) -> dict[str, Any]:
    """Lo schema JSON del quaderno, per l'uscita vincolata del modello."""
    proprieta: dict[str, Any] = {}
    for chiave, _titolo, tipo, descrizione in SECTIONS:
        if tipo == TEXT:
            proprieta[chiave] = {"type": "string", "description": descrizione}
        elif tipo == LIST:
            proprieta[chiave] = {"type": "array", "items": {"type": "string"},
                                 "description": descrizione}
        elif tipo == PEOPLE:
            proprieta[chiave] = {"type": "array", "items": _object(_PERSON_FIELDS),
                                 "description": descrizione}
        else:
            proprieta[chiave] = {"type": "array", "items": _object(_PLACE_FIELDS),
                                 "description": descrizione}
    if with_removed:
        proprieta["removed"] = {
            "type": "array", "items": {"type": "string"},
            "description": "nomi di personaggi o luoghi, o voci, che l'autore ha tolto dal libro",
        }
    return {"type": "object", "additionalProperties": False, "properties": proprieta,
            "required": list(proprieta)}


# --------------------------------------------------------------------------- #
#  forma e fusione
# --------------------------------------------------------------------------- #
def empty() -> dict[str, Any]:
    return {k: ("" if KIND[k] == TEXT else []) for k in KEYS}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _items(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    visti: list[str] = []
    for voce in value:
        testo = _text(voce)
        if testo and testo not in visti:
            visti.append(testo)
    return visti


def _records(value: Any, fields: list[tuple[str, str]]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    trovati: list[dict[str, str]] = []
    for voce in value:
        if not isinstance(voce, dict):
            continue
        record = {k: _text(voce.get(k)) for k, _ in fields}
        if record["name"]:
            trovati.append(record)
    return trovati


def normalise(raw: Any) -> dict[str, Any]:
    """Un quaderno con tutte le voci e i tipi giusti, qualunque cosa sia arrivato."""
    raw = raw if isinstance(raw, dict) else {}
    quaderno: dict[str, Any] = {}
    for chiave in KEYS:
        tipo = KIND[chiave]
        if tipo == TEXT:
            quaderno[chiave] = _text(raw.get(chiave))
        elif tipo == LIST:
            quaderno[chiave] = _items(raw.get(chiave))
        elif tipo == PEOPLE:
            quaderno[chiave] = _records(raw.get(chiave), _PERSON_FIELDS)
        else:
            quaderno[chiave] = _records(raw.get(chiave), _PLACE_FIELDS)
    return quaderno


def _key(name: str) -> str:
    return re.sub(r"\W+", " ", (name or "").lower()).strip()


def _merge_records(vecchi: list[dict[str, str]], nuovi: list[dict[str, str]],
                   tolti: set[str]) -> list[dict[str, str]]:
    """Fonde per nome. Un personaggio che il modello si è scordato di ripetere non sparisce:
    sparisce solo quello che l'autore ha tolto."""
    per_nome = {_key(r["name"]): r for r in vecchi}
    fusi: list[dict[str, str]] = []
    for nuovo in nuovi:
        vecchio = per_nome.pop(_key(nuovo["name"]), {})
        fusi.append({k: nuovo.get(k) or vecchio.get(k, "") for k in nuovo})
    fusi += [r for chiave, r in per_nome.items() if chiave not in tolti]
    return [r for r in fusi if _key(r["name"]) not in tolti]


def merge(old: dict[str, Any], new: dict[str, Any], removed: list[str] | None = None) -> dict[str, Any]:
    """Il quaderno aggiornato, senza perdere quello che il modello ha lasciato cadere per strada.

    Il modello restituisce il quaderno intero, e un modello che riscrive venti voci ogni tanto ne
    svuota una per distrazione. Quello che l'autore ha detto non si perde così: una voce piena
    resta piena finché non arriva un valore nuovo, e un nome sparisce solo se è fra i ``removed``.
    Fanno eccezione le questioni aperte, che quando si risolvono escono dall'elenco.
    """
    old, new = normalise(old), normalise(new)
    tolti = {_key(n) for n in (removed or []) if _key(n)}
    fuso: dict[str, Any] = {}
    for chiave in KEYS:
        tipo, prima, dopo = KIND[chiave], old[chiave], new[chiave]
        if tipo in (PEOPLE, PLACES):
            fuso[chiave] = _merge_records(prima, dopo, tolti)
        elif tipo == LIST:
            if dopo or chiave in CLEARABLE:
                fuso[chiave] = [v for v in dopo if _key(v) not in tolti]
            else:
                fuso[chiave] = [v for v in prima if _key(v) not in tolti]
        else:
            fuso[chiave] = dopo or prima
    return fuso


# --------------------------------------------------------------------------- #
#  lettura
# --------------------------------------------------------------------------- #
def is_empty(notebook: dict[str, Any] | None) -> bool:
    quaderno = normalise(notebook)
    return not any(quaderno[k] for k in KEYS)


def missing(notebook: dict[str, Any] | None) -> list[str]:
    """Le voci portanti ancora vuote, per titolo."""
    quaderno = normalise(notebook)
    return [LABEL[k] for k in CORE if not quaderno[k]]


def filled(notebook: dict[str, Any] | None) -> tuple[int, int]:
    """``(voci portanti piene, voci portanti)``, per dire a che punto è la conversazione."""
    quaderno = normalise(notebook)
    return sum(1 for k in CORE if quaderno[k]), len(CORE)


def render(notebook: dict[str, Any] | None, *, level: int = 2, skip: tuple[str, ...] = ()) -> str:
    """Il quaderno in markdown: per il pannello accanto alla conversazione e per i prompt."""
    quaderno = normalise(notebook)
    cancelletti = "#" * level
    blocchi: list[str] = []
    for chiave, titolo, tipo, _ in SECTIONS:
        valore = quaderno[chiave]
        if not valore or chiave in skip:
            continue
        if chiave == "language":
            valore = language_label(valore)
        if tipo == TEXT:
            corpo = valore
        elif tipo == LIST:
            corpo = "\n".join(f"- {v}" for v in valore)
        elif tipo == PEOPLE:
            corpo = "\n".join(_render_person(p) for p in valore)
        else:
            corpo = "\n".join(
                f"- **{p['name']}**" + (f" — {p['description']}" if p["description"] else "")
                for p in valore)
        blocchi.append(f"{cancelletti} {titolo}\n\n{corpo}")
    return "\n\n".join(blocchi)


def _render_person(p: dict[str, str]) -> str:
    testa = f"- **{p['name']}**" + (f" — {p['role']}" if p.get("role") else "")
    dettagli = [(e, p.get(k, "")) for k, e in _PERSON_FIELDS if k not in ("name", "role")]
    return "\n".join([testa] + [f"  - *{e}:* {v}" for e, v in dettagli if v])


# --------------------------------------------------------------------------- #
#  quello che le altre fasi chiedono al libro
# --------------------------------------------------------------------------- #
#: Le risposte della vecchia intervista a domande, in un libro cominciato prima: id → voce.
_LEGACY = {"title": "identita.titolo", "form": "libro.forma", "premise": "libro.una_frase",
           "language": "libro.lingua", "audience": "lettore.chi", "length": "oggetto.lunghezza",
           "messages": "libro.tesi", "reader_effect": "libro.cambiamento"}

#: Le vecchie risposte che riguardavano la stampa: non dicono niente su come scrivere il libro.
_LEGACY_PRINT = ("oggetto.", "canale.", "produzione.")


def get(state: Any, key: str) -> str:
    """Una voce di testo del quaderno; per un libro cominciato con le domande, la vecchia risposta."""
    valore = normalise(getattr(state, "notebook", None))[key]
    if isinstance(valore, list):
        valore = "; ".join(v if isinstance(v, str) else v.get("name", "") for v in valore)
    if valore:
        return valore
    vecchio = _LEGACY.get(key)
    return ((getattr(state, "answers", None) or {}).get(vecchio) or "").strip() if vecchio else ""


def language_code(state: Any) -> str:
    """La lingua del libro come codice: dal quaderno, poi la lingua in cui si sta parlando."""
    valore = (get(state, "language") or getattr(state, "language", "") or "it").strip().lower()
    # Prima il codice così com'è: ``language_tag`` cerca anche dentro i nomi, e «es» sta dentro
    # «inglese» — un libro spagnolo uscirebbe con la sillabazione inglese.
    if valore in {c for c, _ in SUPPORTED_LANGUAGES if c}:
        return valore
    codice = language_tag(valore).lower()
    return codice if len(codice) == 2 else "it"


def language_name(state: Any) -> str:
    """La lingua del libro per i prompt: «italiano», «inglese (English)»…"""
    return language_label(language_code(state)) or "italiano"


def legacy_answers(state: Any) -> str:
    """Le risposte della vecchia intervista a domande che dicono qualcosa sul libro, in elenco."""
    vecchie = {k: v.strip() for k, v in (getattr(state, "answers", None) or {}).items()
               if (v or "").strip() and not k.startswith(_LEGACY_PRINT)}
    return "\n".join(f"- **{k.replace('.', ' · ').replace('_', ' ')}**: {v}"
                     for k, v in vecchie.items())


def brief(state: Any) -> str:
    """Tutto quello che si sa del libro, per i prompt che lo devono conoscere."""
    testo = render(getattr(state, "notebook", None), skip=("language",))
    if vecchie := legacy_answers(state):
        testo = (testo + "\n\n" if testo else "") + "## Dall'intervista precedente\n\n" + vecchie
    return testo or "(della conversazione con l'autore non è rimasto niente)"


#: Le forme in cui il libro si racconta per scene: per queste la stesura progetta scene (chi vuole
#: cosa, cosa lo ostacola, cosa cambia), non argomenti. Le altre (saggio, manuale…) argomentano.
_NARRATIVE = re.compile(
    r"romanz|giall|thriller|noir|fantasy|fantascien|horror|raccont|novel|novella|fiction|"
    r"narrativ|avventur|fiaba|favol|saga|mystery|romance|story|stories", re.IGNORECASE)


def is_narrative(state: Any) -> bool:
    """Se il libro è un racconto (romanzo, giallo, racconti…) e non un saggio o un manuale."""
    return bool(_NARRATIVE.search(get(state, "form")))


def protagonist(state: Any) -> str:
    """Il nome del protagonista: il personaggio che il quaderno dice tale, o il primo."""
    persone = normalise(getattr(state, "notebook", None)).get("characters") or []
    for p in persone:
        if re.search(r"protagonist", str(p.get("role", "")), re.IGNORECASE):
            return str(p.get("name", "")).strip()
    return str(persone[0].get("name", "")).strip() if persone else ""
