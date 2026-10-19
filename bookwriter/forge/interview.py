"""Fase 1 — la conversazione: capire il libro che l'autore ha in mente, e renderlo migliore.

Non è un questionario. L'autore racconta il libro come lo racconterebbe a un editor, e
l'assistente fa quello che fa un buon editor: domanda una cosa alla volta, legata a quello che
ha appena sentito; allunga la trama chiedendo cosa succede dopo e perché; si accorge dei buchi —
un fatto che non ha causa, un personaggio che agisce senza motivo, un finale che contraddice il
messaggio — e li dice; propone, e aspetta il sì dell'autore prima di considerare deciso.

Quello che emerge finisce nel **quaderno del libro** (:mod:`notebook`), che l'assistente
aggiorna dopo ogni scambio e l'autore vede crescere accanto alla conversazione.

Di stampa non si parla: misura, margini, copertina, rilegatura e canale si decidono a mano nella
fase di produzione, e chiederli qui sarebbe chiedere cose che nessuno userebbe. Lo stile lo
sceglie l'autore nella tendina: l'assistente ne conosce il profilo, e al più ne chiede una
precisazione o suggerisce un ritocco.

La conversazione la chiude l'autore, quando ha detto quello che gli preme. Da lì il resto lo
decide l'assistente: riempie il quaderno, scioglie le questioni aperte, e dice cosa ha deciso.

Qui ci si discosta di proposito da *book-forge* (``assets/references/interview.md``), che
intervista a gruppi di domande fisse e mette la stampa fra le decisioni portanti: in questo
programma la stampa ha la sua fase e i suoi comandi, e un questionario uguale per ogni libro
chiedeva proprio le cose che non servono e taceva su quelle che fanno un libro.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from bookwriter.config import language_label
from bookwriter.forge import notebook as nb
from bookwriter.forge.project import ForgeState

#: ``(tipo, testo)``: «content» per la risposta che arriva, «reasoning» per il pensiero,
#: «restart» quando la richiesta riparte da capo e quello arrivato finora va buttato.
OnDelta = Callable[[str, str], None]

#: Quanta conversazione si rimanda al modello a ogni battuta, in caratteri. Il resto non è perso:
#: sta nel quaderno, che va sempre per intero.
HISTORY_CHARS = 60_000

#: Quanta conversazione legge la passata finale, che deve vederla tutta o quasi.
FINAL_CHARS = 120_000

#: La prima battuta, quando la conversazione comincia. Non serve un modello per dirla.
OPENING = (
    "Raccontami il libro che hai in mente, come lo racconteresti a un editor: di cosa parla, "
    "cosa succede, chi lo abita, dove vuoi portare chi lo legge. Va benissimo anche un'idea "
    "appena abbozzata — la sviluppiamo insieme.\n\n"
    "Ti farò domande e ti proporrò strade; quando avrai detto quello che ti preme, chiudi la "
    "conversazione e il resto lo decido io."
)


class SupportsChat(Protocol):
    def chat(self, messages: list[dict[str, str]], *, model: str | None = ...,
             extra: dict[str, Any] | None = ...,
             on_delta: OnDelta | None = ...) -> str:
        ...

    def chat_json(self, messages: list[dict[str, str]], *, schema: dict,
                  model: str | None = ...) -> Any:
        ...


class Stopped(Exception):
    """L'autore ha fermato la risposta mentre arrivava."""


# --------------------------------------------------------------------------- #
#  i prompt
# --------------------------------------------------------------------------- #
_ROLE = (
    "Sei un editor esperto che lavora accanto all'autore di un libro. Il libro lo scriverà "
    "poi un modello, a partire da quello che emerge qui: il tuo compito è capirlo a fondo e "
    "aiutare l'autore a renderlo migliore. Non stai compilando un modulo."
)

_TERRITORY = (
    "## Cosa ti serve capire\n"
    "Il territorio, non una lista da spuntare in ordine — segui la conversazione:\n"
    "- cosa vuole ottenere l'autore con questo libro, e come arrivarci; se ci sono messaggi "
    "da far passare; che effetto deve fare a chi lo legge;\n"
    "- chi è il lettore: da lui dipendono linguaggio, registro, complessità del racconto e "
    "lunghezza dei capitoli. Quando l'hai capito, di' cosa ne consegue e verificalo;\n"
    "- la trama: allungala facendo domande e proponendo — cosa succede dopo, cosa c'era "
    "prima; la catena degli avvenimenti e i passaggi logici; i nessi causali (perché "
    "succede quel che succede, cosa lo rende credibile); i meccanismi psicologici "
    "(motivazioni, paure, desideri, conflitti, come cambiano le persone) e quelli narrativi "
    "(tensione, rivelazioni, punto di vista, ritmo);\n"
    "- gli avvenimenti che l'autore vuole assolutamente, le scene e i paragrafi chiave, il "
    "finale che desidera;\n"
    "- i personaggi: aspetto fisico, psicologia, carattere, cultura e origini, relazioni, "
    "come cambiano;\n"
    "- i luoghi, gli ambienti, l'epoca, le regole del mondo;\n"
    "- qualunque altra cosa ti serva per scriverlo bene.\n"
    "Se il libro non è narrativa, traduci: la linea argomentativa al posto della trama, i "
    "casi e gli esempi al posto delle scene, le persone reali al posto dei personaggi."
)

_DO_NOT_ASK = (
    "## Cosa non chiedere\n"
    "Niente sulla stampa e sull'oggetto: misura della pagina, margini, copertina, "
    "rilegatura, carta, colore, ISBN, canale di pubblicazione, prezzo. Si decidono altrove, "
    "a mano. Non chiedere quale stile usare: lo stile è già scelto. Sullo stile puoi solo "
    "chiedere una precisazione o dare un suggerimento, quando serve al libro."
)

_CONDUCT = (
    "## Come condurre\n"
    "- È una conversazione. Una domanda per volta, due se sono strettamente legate, e "
    "sempre legate a quello che l'autore ha appena detto.\n"
    "- Non limitarti a domandare: proponi. Sviluppi della trama, alternative, conseguenze; "
    "segnala i rischi (un buco logico, un personaggio senza motivazione, un finale che "
    "contraddice il messaggio, un registro sbagliato per quel lettore). Motiva in breve e "
    "chiedi se l'autore è d'accordo; quando lo è, dillo in una riga («D'accordo: …»).\n"
    "- Le tue proposte restano proposte finché l'autore non le accetta. Su quello a cui "
    "tiene decide lui.\n"
    "- Non chiedere di nuovo quello che l'autore ha già detto: è nel quaderno.\n"
    "- Ogni tanto, non a ogni battuta, fai il punto in poche righe.\n"
    "- Risposte brevi e dense, in markdown leggero (grassetto, elenchi corti). Niente "
    "preamboli, niente complimenti.\n"
    "- A chiudere la conversazione è l'autore: non spingerlo. Se il quadro ti sembra "
    "completo puoi dirlo, una volta.\n"
    "- Tu qui puoi solo parlare: non puoi avviare, chiudere, scrivere né salvare niente. "
    "Non dire mai che «ti metti al lavoro», che «chiudi qui» o che «scrivi» il libro: sarebbe "
    "una promessa che non puoi mantenere. Quando il quadro ti sembra completo, o l'autore "
    "ti chiede di cominciare, digli che basta premere il pulsante «Ho finito: decidi tu il "
    "resto»: chiude la conversazione e ti lascia decidere tutto il resto. Dillo quando serve, "
    "non a ogni battuta."
)


def system_prompt(state: ForgeState, *, style: str = "", trimmed: bool = False) -> str:
    """Il ruolo dell'assistente, con il quaderno e lo stile scelto: lo stato del lavoro."""
    lingua = language_label(state.language) or "italiano"
    quaderno = nb.render(state.notebook) or "(ancora vuoto)"
    scoperte = nb.missing(state.notebook)
    stile = (f"L'autore ha scelto nell'interfaccia questo stile, che vale per tutto il libro:\n"
             f"{style.strip()}" if style.strip() else
             "Nell'interfaccia non è scelto nessuno stile particolare: il libro sarà scritto in "
             "prosa chiara.")
    parti = [
        _ROLE,
        _TERRITORY,
        _DO_NOT_ASK,
        f"## Lo stile\n{stile}",
        _CONDUCT,
        f"## Il quaderno del libro finora\n{quaderno}",
    ]
    if scoperte:
        parti.append("## Ancora scoperto\n" + ", ".join(scoperte) +
                     "\nNon serve chiederle tutte, né di seguito: ci si arriva dalla conversazione.")
    if trimmed:
        parti.append("La parte più vecchia della conversazione non è qui sotto: quello che conta "
                     "è nel quaderno.")
    parti.append(f"Rispondi nella lingua in cui ti scrive l'autore; se non è chiaro, in {lingua}.")
    return "\n\n".join(parti)


def _alternate(transcript: list[dict[str, str]], *, limit: int) -> tuple[list[dict[str, str]], bool]:
    """La conversazione come messaggi per il modello: alternati, e cominciando dall'autore.

    Diversi modelli rifiutano due battute di fila dello stesso ruolo, o una conversazione che
    comincia dall'assistente: le battute consecutive si uniscono, e l'apertura (che non dice
    niente del libro) resta fuori.
    """
    messaggi: list[dict[str, str]] = []
    for battuta in transcript:
        ruolo = "assistant" if battuta.get("role") == "assistant" else "user"
        testo = (battuta.get("text") or "").strip()
        if not testo:
            continue
        if messaggi and messaggi[-1]["role"] == ruolo:
            messaggi[-1]["content"] += "\n\n" + testo
        else:
            messaggi.append({"role": ruolo, "content": testo})
    tagliata = False
    while messaggi and sum(len(m["content"]) for m in messaggi) > limit and len(messaggi) > 1:
        messaggi.pop(0)
        tagliata = True
    while messaggi and messaggi[0]["role"] != "user":
        messaggi.pop(0)
    return messaggi, tagliata


# --------------------------------------------------------------------------- #
#  una battuta
# --------------------------------------------------------------------------- #
def reply(
    state: ForgeState,
    client: SupportsChat,
    text: str | None,
    *,
    model: str | None = None,
    style: str = "",
    opening: str = "",
    on_delta: OnDelta | None = None,
) -> str:
    """Registra quello che ha scritto l'autore e ritorna la risposta dell'assistente.

    ``text`` vuoto vuol dire «riprova»: si risponde di nuovo all'ultima battuta dell'autore, che
    è già nella conversazione. ``opening`` è la frase con cui l'assistente ha aperto, da mettere
    in testa alla conversazione se questa è la prima battuta.

    Se l'autore ferma la risposta a metà (``on_delta`` solleva :class:`Stopped`), quello che era
    già arrivato resta nella conversazione: l'ha letto, e può volerci rispondere.
    """
    if not state.transcript and opening.strip():
        state.say("assistant", opening)
    if text and text.strip():
        state.say("user", text)
    if not state.transcript or state.transcript[-1].get("role") != "user":
        raise ValueError("Non c'è niente a cui rispondere: scrivi prima qualcosa del libro.")

    messaggi, tagliata = _alternate(state.transcript, limit=HISTORY_CHARS)
    arrivato: list[str] = []

    def ascolta(tipo: str, pezzo: str) -> None:
        # Prima chi mostra, poi il conto: se l'autore ferma proprio qui, il pezzo che non ha
        # visto non deve finire nella conversazione salvata.
        if on_delta is not None:
            on_delta(tipo, pezzo)
        if tipo == "restart":
            arrivato.clear()
        elif tipo == "content":
            arrivato.append(pezzo)

    try:
        risposta = client.chat(
            [{"role": "system", "content": system_prompt(state, style=style, trimmed=tagliata)},
             *messaggi],
            model=model, on_delta=ascolta,
        ).strip()
    except Stopped:
        parziale = "".join(arrivato).strip()
        if parziale:
            state.say("assistant", parziale + " …")
        raise
    state.say("assistant", risposta)
    return risposta


# --------------------------------------------------------------------------- #
#  il quaderno
# --------------------------------------------------------------------------- #
_UPDATE_RULES = (
    "Tieni il quaderno di lavoro di un libro, mentre l'autore ne parla con un editor. Ti do il "
    "quaderno com'è adesso e l'ultimo tratto della conversazione: restituisci il quaderno "
    "aggiornato, intero.\n"
    "- Conserva tutto quello che è ancora valido, con le stesse parole se non è cambiato.\n"
    "- Aggiungi quello che l'autore ha detto, e correggi quello che ha cambiato.\n"
    "- Le proposte dell'editor entrano nel quaderno solo se l'autore le ha accettate; finché "
    "non ha risposto, vanno in 'open_questions' come «Proposta: …». Una proposta accettata va "
    "nella sua voce e, in una riga, in 'agreed'.\n"
    "- Togli da 'open_questions' quello che è stato deciso.\n"
    "- Non inventare e non completare: una voce di cui non si è parlato resta vuota (stringa "
    "vuota \"\" per i testi, elenco vuoto [] per gli elenchi: la chiave c'è sempre).\n"
    "- 'language' è il codice ISO 639-1 della lingua in cui va scritto il libro; se nessuno "
    "l'ha detto, è la lingua in cui parla l'autore.\n"
    "- 'style_notes' raccoglie solo precisazioni sullo stile; lo stile si sceglie altrove.\n"
    "- In 'removed' metti i nomi di personaggi, luoghi o voci che l'autore ha tolto dal libro.\n"
    "- Nessuna voce sulla stampa: misura, copertina, carta e canale non stanno nel quaderno.\n"
    "- '{close_key}' è vero solo se, nell'ultimo tratto, l'AUTORE ha chiesto in modo esplicito di "
    "chiudere la conversazione, di cominciare il lavoro o di lasciar decidere a te il resto "
    "(«basta così, procedi», «puoi iniziare il lavoro»). Non conta un'ipotesi dell'editor, né "
    "una frase come «iniziamo a parlare dei personaggi»: nel dubbio è falso. Non è una voce "
    "del quaderno.\n"
    "Scrivi nella lingua in cui parla l'autore (di norma {lingua}), tranne 'language'."
)

#: Il campo con cui l'aggiornamento dice se l'autore ha chiesto di chiudere. Non è una voce del
#: quaderno: lo schema della passata finale non lo ha, e ``merge`` non lo vede.
CLOSE_KEY = "author_asked_to_close"


def update_schema() -> dict:
    """Lo schema del quaderno più la domanda «l'autore ha chiesto di chiudere?».

    Strict come l'altro: ogni proprietà è in ``required`` e non ne sono ammesse altre.
    """
    schema = nb.schema()
    schema["properties"][CLOSE_KEY] = {
        "type": "boolean",
        "description": "vero solo se l'autore, nell'ultimo tratto, ha chiesto esplicitamente di "
                       "chiudere la conversazione, di cominciare il lavoro o di lasciar decidere "
                       "il resto all'editor",
    }
    schema["required"] = list(schema["properties"])
    return schema


@dataclass(frozen=True)
class NotebookUpdate:
    """Esito di un aggiornamento del quaderno."""

    read: bool = False  #: il quaderno è stato aggiornato
    close_requested: bool = False  #: l'autore ha chiesto di chiudere la conversazione


def update_notebook(state: ForgeState, client: SupportsChat, *,
                    model: str | None = None) -> NotebookUpdate:
    """Porta il quaderno al passo con la conversazione.

    Si legge da dove il quaderno era rimasto (``state.notebook_upto``), partendo dalla battuta
    dell'assistente che precede: è lì che di solito sta la proposta a cui l'autore dice di sì.
    Se un aggiornamento non riesce, il successivo riprende anche quel tratto: niente si perde
    perché il modello una volta non ha risposto.

    Nella stessa chiamata il modello dice se l'autore ha chiesto di chiudere la conversazione
    (``close_requested``): la chat non può avviare azioni, e qualcuno deve accorgersene.
    """
    dove = max(0, min(state.notebook_upto, len(state.transcript)) - 1)
    tratto = state.transcript[dove:]
    if not any(b.get("role") == "user" for b in tratto):
        return NotebookUpdate()
    conversazione = "\n\n".join(
        f"{'EDITOR' if b.get('role') == 'assistant' else 'AUTORE'}: {b.get('text', '').strip()}"
        for b in tratto
    )
    lingua = language_label(state.language) or "italiano"
    utente = (
        "## Il quaderno com'è adesso\n```json\n"
        + json.dumps(nb.normalise(state.notebook), ensure_ascii=False, indent=1)
        + f"\n```\n\n## L'ultimo tratto della conversazione\n{conversazione}"
    )
    risultato = client.chat_json(
        [{"role": "system", "content": _UPDATE_RULES.format(lingua=lingua, close_key=CLOSE_KEY)},
         {"role": "user", "content": utente}],
        schema=update_schema(), model=model,
    )
    if not isinstance(risultato, dict):
        return NotebookUpdate()
    state.notebook = nb.merge(state.notebook, risultato, risultato.get("removed") or [])
    state.notebook_upto = len(state.transcript)
    # Solo un vero «sì»: una stringa o un numero capitati per errore non chiudono il libro.
    return NotebookUpdate(read=True, close_requested=risultato.get(CLOSE_KEY) is True)


_FINAL_RULES = (
    "L'autore ha chiuso la conversazione con l'editor e lascia a te tutto il resto. Ti do il "
    "quaderno del libro e la conversazione: restituisci il quaderno completo, pronto perché il "
    "libro si possa progettare e scrivere.\n"
    "- Quello che l'autore ha detto o accettato non si tocca: è suo.\n"
    "- Riempi ogni voce rimasta vuota che il libro richiede, con la scelta migliore per gli "
    "obiettivi dell'autore e per il suo lettore, coerente con tutto il resto. In un libro non "
    "narrativo traduci le voci (la linea argomentativa al posto della trama, i casi al posto "
    "delle scene).\n"
    "- Sciogli ogni questione aperta e svuota 'open_questions': decidi tu.\n"
    "- Dai ai personaggi principali aspetto, psicologia, cultura e arco; ai luoghi una "
    "descrizione; alla catena degli avvenimenti un ordine che regga, con i nessi causali.\n"
    "- Elenca in 'assistant_decisions' ogni decisione presa da te, una per riga, breve: "
    "l'autore deve poterle rileggere e cambiarle.\n"
    "- 'language' è il codice ISO 639-1 della lingua del libro. Lascia 'removed' come elenco vuoto ([]).\n"
    "- Nessuna voce sulla stampa: misura, copertina, carta e canale si decidono altrove.\n"
    "Scrivi nella lingua in cui parla l'autore (di norma {lingua}), tranne 'language'."
)


def finish(state: ForgeState, client: SupportsChat, *, model: str | None = None,
           style: str = "") -> list[str]:
    """La passata di chiusura: l'assistente decide il resto. Ritorna quello che ha deciso."""
    messaggi, _ = _alternate(state.transcript, limit=FINAL_CHARS)
    conversazione = "\n\n".join(
        f"{'EDITOR' if m['role'] == 'assistant' else 'AUTORE'}: {m['content']}" for m in messaggi
    ) or "(l'autore non ha detto niente: decidi tutto tu, e dillo)"
    lingua = language_label(state.language) or "italiano"
    utente = (
        "## Il quaderno\n```json\n"
        + json.dumps(nb.normalise(state.notebook), ensure_ascii=False, indent=1)
        + "\n```\n\n"
        + (f"## Lo stile scelto nell'interfaccia\n{style.strip()}\n\n" if style.strip() else "")
        + f"## La conversazione\n{conversazione}"
    )
    risultato = client.chat_json(
        [{"role": "system", "content": _FINAL_RULES.format(lingua=lingua)},
         {"role": "user", "content": utente}],
        schema=nb.schema(), model=model,
    )
    if not isinstance(risultato, dict):
        raise ValueError("Il modello non ha restituito il quaderno.")
    prima = nb.normalise(state.notebook)
    # Il modello riceve il quaderno intero e lo restituisce intero, decisioni di ieri comprese.
    # Chi chiude due volte trova nel quaderno tutte le decisioni prese, e nel messaggio solo
    # quelle di adesso: ripresentargli quelle di ieri come nuove lo confonderebbe.
    nuove = [d for d in nb.normalise(risultato)["assistant_decisions"]
             if d not in prima["assistant_decisions"]]
    state.notebook = nb.merge(prima, {**risultato, "open_questions": []})
    state.notebook["assistant_decisions"] = prima["assistant_decisions"] + nuove
    state.notebook_upto = len(state.transcript)
    state.interview_closed = True
    return nuove


def closing_message(decisions: list[str]) -> str:
    """Quello che l'assistente dice chiudendo: cosa ha deciso, e che si può ancora cambiare."""
    if not decisions:
        return ("Il quaderno è completo: non c'era niente da decidere al posto tuo. Lo trovi "
                "qui accanto; se vuoi cambiare qualcosa, scrivimelo — la conversazione resta "
                "aperta.")
    righe = "\n".join(f"- {d}" for d in decisions)
    return ("Ho completato il quaderno. Queste le decisioni che ho preso io:\n\n"
            f"{righe}\n\nIl quaderno intero è qui accanto. Se qualcosa non ti torna, scrivimelo: "
            "la conversazione resta aperta e il quaderno si aggiorna.")


def exchanges(state: ForgeState) -> int:
    """Quante volte ha parlato l'autore."""
    return sum(1 for b in state.transcript if b.get("role") == "user")
