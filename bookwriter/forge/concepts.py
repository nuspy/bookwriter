"""I concetti con un identificativo stabile e i loro nomi in più lingue.

I modelli scrivono lo stesso concetto in modi diversi: nella prova v4 del cap. 11 la catena del
motivo è tornata con ``consequence`` e ``choice`` dove il controllo cercava ``conseguenza`` e
``scelta``, e il controllo ha dichiarato assente un concetto presente. Qui ogni vocabolario chiuso
del sistema (variabili della scena, funzioni della ricerca, passi del motivo, funzioni di una
ricorrenza, gravità, stati di un rilievo, tipi di verità) ha un ID canonico e i suoi alias in
italiano e in inglese; :func:`normalise` riporta un valore all'ID, o restituisce ``""`` se il valore
non è riconosciuto (allora chi chiama lo tratta come ambiguo, non come vero).

Gli alias sono espliciti: una corrispondenza a bassa confidenza non si indovina.
"""

from __future__ import annotations

import re
import unicodedata

#: vocabolario → {ID canonico: alias (in qualunque lingua, senza accenti né maiuscole)}
VOCABULARIES: dict[str, dict[str, tuple[str, ...]]] = {
    "variabile": {
        "conoscenza": ("knowledge", "sapere", "informazione", "information", "understanding"),
        "rischio": ("risk", "pericolo", "danger", "threat", "minaccia"),
        "possesso": ("possession", "oggetto", "object", "custody", "custodia", "item"),
        "rapporto": ("relationship", "relazione", "relation", "alleanza", "alliance", "trust",
                     "fiducia"),
        "destinazione": ("destination", "obiettivo", "goal", "luogo", "place", "direction"),
        "fuga": ("escape", "via d'uscita", "way out", "exit", "uscita"),
    },
    "funzione_ricerca": {
        "enigma": ("puzzle", "mystery", "mistero", "riddle", "clue", "indizio"),
        "decisione": ("decision", "choice", "scelta", "conseguenza", "consequence"),
        "rapporto_di_forza": ("rapporto di forza", "power", "power balance", "leverage",
                              "potere"),
        "semina": ("seed", "foreshadowing", "setup", "plant", "anticipazione"),
        "ambientazione": ("setting", "ambiente", "atmosphere", "atmosfera", "world",
                          "context", "contesto"),
    },
    "passo_motivo": {
        "indizio": ("clue", "hint", "evidence", "prova", "introduzione", "introduction"),
        "conflitto": ("conflict", "obstacle", "ostacolo", "tension"),
        "costo": ("cost", "price", "prezzo", "loss", "perdita"),
        "scelta": ("choice", "decision", "decisione", "dilemma"),
        "conseguenza": ("consequence", "result", "effect", "effetto", "payoff", "esito",
                        "outcome"),
    },
    #: la funzione di una ricorrenza di un motivo (specifica §4, fase 4)
    "funzione_ricorrenza": {
        "introduzione": ("introduction", "introduce", "concept", "concetto"),
        "prova_nuova": ("new evidence", "evidence", "prova", "nuova prova", "clue", "indizio"),
        "reinterpretazione": ("reinterpretation", "reframe", "nuova lettura"),
        "ostacolo": ("obstacle", "conflict", "conflitto"),
        "scelta": ("choice", "decision", "decisione"),
        "conseguenza": ("consequence", "cost", "costo", "effect"),
        "escalation": ("escalation", "escalate", "intensificazione"),
        "payoff": ("payoff", "pay off", "risoluzione", "resolution"),
        "ripetizione": ("repetition", "no progress", "senza avanzamento", "redundant",
                        "ridondanza", "restatement"),
    },
    "gravita": {
        "blocker": ("bloccante", "blocker", "critical", "critico", "blocking"),
        "major": ("maggiore", "major", "importante", "important", "high"),
        "minor": ("minore", "minor", "low", "lieve"),
        "preference": ("preferenza", "preference", "stilistica", "style", "taste", "gusto"),
    },
    "stato_rilievo": {
        "open": ("aperto", "open", "nuovo", "new"),
        "in_repair": ("in riparazione", "in repair", "in corso"),
        "verified": ("verificato", "verified", "resolved", "risolto", "fixed", "pass"),
        "rejected": ("respinto", "rejected", "not applicable", "non applicabile", "false alarm",
                     "falsa allerta"),
        "author": ("richiede autore", "author", "author decision", "decisione dell'autore",
                   "author input"),
    },
    "verita": {
        "canone": ("canon", "canonico", "fiction", "narrativo"),
        "evidenza": ("evidence", "external evidence", "evidenza esterna", "documented",
                     "fonte", "source"),
        "ipotesi": ("hypothesis", "interpretation", "interpretazione", "ipotesi interpretativa"),
        "licenza": ("license", "licenza narrativa", "narrative license", "creative license"),
        "non_verificato": ("unverified", "non verificato", "unknown", "da verificare"),
    },
}


def _plain(testo: str) -> str:
    testo = unicodedata.normalize("NFKD", str(testo or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[\s_\-]+", " ", testo.lower()).strip()


_INDEX: dict[str, dict[str, str]] = {
    vocab: {_plain(alias): ident for ident, aliases in voci.items()
            for alias in (ident, *aliases)}
    for vocab, voci in VOCABULARIES.items()
}


def normalise(vocabulary: str, value: str) -> str:
    """L'ID canonico di ``value`` nel vocabolario, o ``""`` se non è riconosciuto."""
    return _INDEX[vocabulary].get(_plain(value), "")


def ids(vocabulary: str) -> tuple[str, ...]:
    return tuple(VOCABULARIES[vocabulary])


def describe(vocabulary: str) -> str:
    """I valori ammessi, per i prompt e per le descrizioni degli schemi."""
    return ", ".join(VOCABULARIES[vocabulary])
