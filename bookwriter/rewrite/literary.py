"""Traduzione letteraria: modi di dire decisi dall'utente, rifinitura madrelingua, cambi di forma.

Una traduzione corretta parola per parola non è ancora una traduzione letteraria. Mancano tre
cose, e ciascuna vive qui come passaggio separato, piccolo, con un compito solo — perché il
modello locale scrive al più quattro-cinquemila token per risposta e un prompt che chiede tutto
insieme ottiene metà di ogni cosa:

- **modi di dire e riferimenti culturali** (:func:`trova_idiomi`): un pre-passaggio elenca, per
  ogni brano, le espressioni che una resa letterale tradirebbe, ciascuna con 2-4 opzioni di
  conversione spiegate. Non si applica nulla da solo: la scelta è dell'utente, che la dà una
  volta e vale per tutto il documento. Senza scelta il traduttore fa la resa che farebbe
  comunque, e la voce resta registrata come «non decisa», da decidere anche dopo;
- **rifinitura madrelingua** (:func:`rifinisci`): un editor della lingua di arrivo toglie calchi,
  falsi amici, sintassi e ritmo innaturali senza toccare significato, nomi, paragrafi e glossario.
  È sorvegliata: se allunga o accorcia troppo, o rompe la struttura, si tiene la versione di prima;
- **cambi di forma** (:func:`istruzioni_di_forma`): dai versi alla prosa, e la modernizzazione di
  una lingua antica che conserva la voce dell'autore. Con la prosa il numero dei paragrafi non è
  più un vincolo: restano identici solo i capitoli (canti, sezioni), cioè i titoli.

La divisione in unità (:func:`unita_di_lavoro`) fa sì che ogni risposta stia nel limite di uscita
anche quando il testo prodotto è più lungo dell'ingresso, come accade passando dai versi alla prosa.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from bookwriter.llm.client import LLMError
from bookwriter.text_utils import count_tokens

# --------------------------------------------------------------------------- #
#  forma del testo
# --------------------------------------------------------------------------- #
#: Si conserva la forma dell'originale: stesso numero di paragrafi, nello stesso ordine.
FORM_KEEP = "keep"
#: Dai versi alla prosa: versi e terzine diventano capoversi; i capitoli restano quelli.
FORM_PROSE_FROM_VERSE = "prose_from_verse"
FORMS = (FORM_KEEP, FORM_PROSE_FROM_VERSE)

#: Token di testo che una risposta può contenere con margine: il modello locale ne scrive al più
#: quattro-cinquemila, e nella stessa risposta ci stanno anche glossario e riassunto.
BUDGET_USCITA_TOKEN = 3000

#: Di quanto la rifinitura può cambiare la lunghezza prima di essere scartata: oltre, non sta
#: più rifinendo ma riscrivendo (o tagliando).
TOLLERANZA_RIFINITURA = 0.15

_TITOLO = re.compile(r"^\s{0,3}#{1,6}\s")


def espansione(mode: str, form: str, modernize: bool) -> float:
    """Quanto il testo prodotto è più lungo dell'ingresso, in token, nel caso peggiore ragionevole.

    La prosa ricavata dai versi scioglie inversioni ed ellissi e lega i versi fra loro: è
    sensibilmente più lunga. Tradurre costa in media un po' di più (le lingue di arrivo si
    spezzano in più token), modernizzare scioglie i costrutti compressi.
    """
    fattore = 1.0
    if mode == "translate":
        fattore *= 1.25
    if form == FORM_PROSE_FROM_VERSE:
        fattore *= 1.8
    if modernize:
        fattore *= 1.15
    return fattore


def _paragrafi(testo: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", testo or "") if p.strip()]


def _e_titolo(paragrafo: str) -> bool:
    return bool(_TITOLO.match(paragrafo))


def conta_titoli(testo: str) -> int:
    return sum(1 for p in _paragrafi(testo) if _e_titolo(p))


def _verso_singolo(paragrafo: str) -> bool:
    """Un paragrafo che è un verso solo: una riga, breve."""
    return "\n" not in paragrafo.strip() and len(paragrafo) <= 90 and not _e_titolo(paragrafo)


def _gruppi_di_versi(paragrafi: list[str]) -> list[str]:
    """Raccoglie in strofe i versi che l'impaginazione ha messo ciascuno in un paragrafo.

    Molte edizioni (e quasi tutti i DOCX) hanno un verso per paragrafo: mandati così al modello,
    sono cento righe isolate e il senso della terzina si perde. Una sequenza di almeno sei versi
    singoli si raccoglie a gruppi di tre — la terzina, unità minima di senso della Commedia e
    ragionevole per ogni poesia — e le strofe già scritte su più righe restano come sono.
    """
    gruppi: list[str] = []
    corsa: list[str] = []

    def chiudi() -> None:
        if len(corsa) >= 6:
            for i in range(0, len(corsa), 3):
                gruppi.append("\n".join(corsa[i : i + 3]))
        else:
            gruppi.extend(corsa)
        corsa.clear()

    for p in paragrafi:
        if _verso_singolo(p):
            corsa.append(p)
        else:
            chiudi()
            gruppi.append(p)
    chiudi()
    return gruppi


def _spezza_strofa(strofa: str, limite: float) -> list[str]:
    """Una strofa troppo lunga per una risposta, divisa a terzine (o al singolo verso)."""
    versi = [v for v in strofa.split("\n") if v.strip()]
    pezzi: list[str] = []
    corrente: list[str] = []
    for i in range(0, len(versi), 3):
        terzina = versi[i : i + 3]
        if corrente and count_tokens("\n".join(corrente + terzina)) > limite:
            pezzi.append("\n".join(corrente))
            corrente = []
        corrente.extend(terzina)
    if corrente:
        pezzi.append("\n".join(corrente))
    return pezzi


def unita_di_lavoro(
    testo: str, *, fattore: float, form: str = FORM_KEEP, budget: int = BUDGET_USCITA_TOKEN
) -> list[str]:
    """Il brano diviso in unità la cui uscita prevista sta dentro il limite di una risposta.

    Si taglia solo su confini che non rompono nulla: fra paragrafi quando la forma si conserva
    (ogni paragrafo resta intero, e l'allineamento coi segmenti del libro continua a tornare);
    con la prosa dai versi anche fra strofe e terzine, perché lì i paragrafi non contano più.
    Nella prosa dai versi i versi isolati si raccolgono comunque in terzine, anche quando il
    brano sta in una risposta sola: è la forma in cui il modello li legge meglio.
    """
    prosa = form == FORM_PROSE_FROM_VERSE
    paragrafi = _paragrafi(testo)
    if prosa:
        paragrafi = _gruppi_di_versi(paragrafi)
    limite = max(200.0, budget / max(fattore, 0.1))
    if count_tokens(testo) <= limite:
        return ["\n\n".join(paragrafi)] if prosa and paragrafi else [testo]
    pezzi: list[str] = []
    for p in paragrafi:
        if prosa and count_tokens(p) > limite and "\n" in p:
            pezzi.extend(_spezza_strofa(p, limite))
        else:
            pezzi.append(p)
    unita: list[str] = []
    corrente: list[str] = []
    for p in pezzi:
        if corrente and count_tokens("\n\n".join([*corrente, p])) > limite:
            unita.append("\n\n".join(corrente))
            corrente = []
        corrente.append(p)
    if corrente:
        unita.append("\n\n".join(corrente))
    return unita or [testo]


def istruzioni_di_forma(
    mode: str, target_language: str | None, *, form: str = FORM_KEEP, modernize: bool = False
) -> str:
    """Le istruzioni sulla forma e sulla lingua che sostituiscono (o affiancano) l'impaginazione."""
    parti: list[str] = []
    if form == FORM_PROSE_FROM_VERSE:
        parti.append(
            "DA VERSI A PROSA: il testo è in versi; rendilo in prosa scorrevole, a capoversi "
            "pieni. Unisci versi, terzine e strofe in paragrafi di prosa secondo il senso (mai un "
            "paragrafo per verso); il numero dei paragrafi NON deve corrispondere all'originale e "
            "la prosa può essere più lunga dei versi. Non togliere né aggiungere contenuto: ogni "
            "immagine, figura, personaggio, battuta e passaggio del racconto resta, nello stesso "
            "ordine. Un paragrafo che comincia con dei cancelletti (## …) è un titolo (canto, "
            "capitolo, sezione): riportalo come titolo, al suo posto, coi suoi cancelletti; i "
            "titoli restano gli stessi, nello stesso numero e nello stesso ordine. Gli asterischi "
            "(*corsivo*, **grassetto**) vanno riportati sulle parole corrispondenti."
        )
    if modernize:
        lingua = (
            f"della lingua di arrivo ({target_language}) di oggi"
            if mode == "translate" and target_language else "della lingua di oggi"
        )
        parti.append(
            f"MODERNIZZA LA LINGUA: usa grammatica, sintassi e ortografia {lingua} e un lessico "
            "comprensibile a un lettore contemporaneo; sciogli inversioni, forme arcaiche, "
            "costrutti latineggianti e allusioni oscure dove impediscono di capire. Conserva però "
            "la VOCE dell'autore: le sue immagini, metafore e similitudini, il tono, il ritmo del "
            "racconto e il sapore del suo lessico — le parole più caratteristiche restano, rese "
            "chiare dal contesto, non sostituite da sinonimi piatti. Non è un riassunto né una "
            "parafrasi scolastica: è lo stesso autore che scrive con la grammatica di oggi."
        )
    return " ".join(parti)


def nota_per_il_giudice(form: str, modernize: bool) -> str:
    """Cosa l'utente ha chiesto di cambiare: il giudice di stile non deve contarlo come difetto."""
    voci = []
    if form == FORM_PROSE_FROM_VERSE:
        voci.append("i versi diventano prosa (paragrafi diversi per numero e lunghezza)")
    if modernize:
        voci.append("grammatica, sintassi e lessico modernizzati conservando la voce dell'autore")
    if not voci:
        return ""
    return "Trasformazioni chieste dall'utente, che NON sono violazioni: " + "; ".join(voci) + "."


# --------------------------------------------------------------------------- #
#  modi di dire e riferimenti culturali
# --------------------------------------------------------------------------- #
IDIOMI_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "voci": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "originale": {"type": "string"},
                    "contesto": {"type": "string"},
                    "tipo": {"type": "string"},
                    "opzioni": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "tipo": {"type": "string"},
                                "resa": {"type": "string"},
                                "spiegazione": {"type": "string"},
                            },
                            "required": ["tipo", "resa", "spiegazione"],
                        },
                    },
                },
                "required": ["originale", "contesto", "tipo", "opzioni"],
            },
        }
    },
    "required": ["voci"],
}

#: Quante voci al più per brano: la risposta resta piccola, e un brano con più di otto modi di
#: dire intraducibili è raro — meglio i più importanti che un elenco che non finisce.
MAX_VOCI = 8

#: Lo stato di una voce nel registro della lavorazione.
DECISA = "decisa"
NON_DECISA = "non decisa"


def chiave_idioma(originale: str) -> str:
    """La stessa espressione, comunque scritta, è la stessa decisione."""
    testo = re.sub(r"\s+", " ", (originale or "").casefold()).strip()
    return testo.strip(" \t\"'«»“”‘’.,;:!?…()[]—–-")


def trova_idiomi(
    testo: str,
    target_language: str | None,
    client: Any,
    model: str | None,
    *,
    note: bool = False,
) -> list[dict]:
    """Pre-passaggio: modi di dire, proverbi, giochi di parole e riferimenti culturali del brano.

    Per ciascuno: l'espressione esatta, la frase in cui sta, e 2-4 opzioni di resa spiegate.
    Le note a piè di pagina compaiono fra le opzioni solo se l'utente le ha chieste (``note``):
    altrimenti un riferimento senza equivalente si rende comprensibile dentro il testo.
    """
    lingua = target_language or "lingua di arrivo"
    opzioni = (
        "equivalente nella cultura di arrivo (un modo di dire che dica la stessa cosa con lo "
        "stesso tono), resa letterale, resa esplicativa senza note (il senso detto dentro la "
        "frase), mantenere il riferimento rendendolo comprensibile dal contesto"
    )
    if note:
        opzioni += ", mantenere il riferimento con una breve nota del traduttore fra parentesi quadre"
    system = (
        f"Sei un traduttore letterario verso {lingua}. Nel BRANO individua i modi di dire, i "
        "proverbi, i giochi di parole e i riferimenti legati alla cultura di partenza (usanze, "
        "personaggi, luoghi, citazioni, istituzioni) che una traduzione letterale renderebbe "
        "oscuri, sbagliati o piatti. Ignora i nomi propri e le parole ordinarie che si "
        "traducono senza problemi. Per ognuno indica: 'originale' (l'espressione ESATTA come "
        "compare nel brano), 'contesto' (la frase in cui compare), 'tipo' (modo di dire, "
        "proverbio, gioco di parole, riferimento culturale) e da 2 a 4 'opzioni' di resa in "
        f"{lingua}, scelte fra: {opzioni}. Ogni opzione ha 'tipo', 'resa' (il testo esatto da "
        f"usare in {lingua}, con significato, registro e tono dell'originale) e 'spiegazione' "
        f"(una frase: cosa guadagna e cosa perde). Al massimo {MAX_VOCI} voci, le più "
        "importanti. Se non ce ne sono, 'voci' è un elenco vuoto."
    )
    risposta = client.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": f"BRANO:\n{testo}"}],
        schema=IDIOMI_SCHEMA,
        model=model,
    )
    voci: list[dict] = []
    visti: set[str] = set()
    for voce in (risposta or {}).get("voci") or []:
        if not isinstance(voce, dict):
            continue
        originale = str(voce.get("originale") or "").strip()
        chiave = chiave_idioma(originale)
        if not chiave or chiave in visti:
            continue
        rese = [
            {
                "tipo": str(o.get("tipo") or "").strip(),
                "resa": str(o.get("resa") or "").strip(),
                "spiegazione": str(o.get("spiegazione") or "").strip(),
            }
            for o in (voce.get("opzioni") or [])
            if isinstance(o, dict) and str(o.get("resa") or "").strip()
        ][:4]
        if not rese:
            continue
        visti.add(chiave)
        voci.append({
            "chiave": chiave,
            "originale": originale,
            "contesto": str(voce.get("contesto") or "").strip(),
            "tipo": str(voce.get("tipo") or "").strip(),
            "opzioni": rese,
        })
        if len(voci) >= MAX_VOCI:
            break
    return voci


def interpreta_scelta(voce: dict, risposta: Any) -> dict | None:
    """La risposta dell'utente come resa da usare, o ``None`` se non ha deciso.

    Si accettano: l'indice di un'opzione (0, 1, …), un dizionario con ``resa`` (e magari
    ``tipo``), o un testo libero — la resa scritta dall'utente stesso.
    """
    if risposta is None or isinstance(risposta, bool):
        return None
    opzioni = voce.get("opzioni") or []
    if isinstance(risposta, int):
        if 0 <= risposta < len(opzioni):
            scelta = opzioni[risposta]
            return {"resa": scelta["resa"], "tipo_scelta": scelta.get("tipo", ""), "opzione": risposta}
        return None
    if isinstance(risposta, dict):
        resa = str(risposta.get("resa") or "").strip()
        if not resa:
            return None
        return {"resa": resa, "tipo_scelta": str(risposta.get("tipo") or "personalizzata"),
                "opzione": risposta.get("opzione")}
    resa = str(risposta).strip()
    if not resa:
        return None
    for i, o in enumerate(opzioni):
        if o.get("resa") == resa:
            return {"resa": resa, "tipo_scelta": o.get("tipo", ""), "opzione": i}
    return {"resa": resa, "tipo_scelta": "personalizzata", "opzione": None}


def registra_idiomi(
    registro: dict,
    voci: list[dict],
    *,
    chunk_id: int,
    book_pos: int | None,
    target_language: str | None,
    decide: Callable[[dict], Any] | None,
) -> tuple[list[dict], int]:
    """Mette le voci del brano nel registro, chiede all'utente quelle nuove, rende le scelte valide.

    Ritorna le scelte vincolanti per questo brano e quante domande sono state fatte. Una voce
    già decisa non si richiede: vale per tutto il documento. Una voce già chiesta e lasciata in
    sospeso non si richiede nemmeno (l'utente ha già detto «non ora»); una mai chiesta — perché
    la prima volta non c'era nessuno a cui chiedere, come nella coda — si chiede adesso.
    """
    scelte: list[dict] = []
    domande = 0
    for voce in voci:
        voce_reg = registro.get(voce["chiave"])
        if voce_reg is None:
            voce_reg = {
                "originale": voce["originale"],
                "contesto": voce["contesto"],
                "tipo": voce["tipo"],
                "opzioni": voce["opzioni"],
                "stato": NON_DECISA,
                "resa": None,
                "tipo_scelta": None,
                "opzione": None,
                "chiesta": False,
                "brani": [],
            }
            registro[voce["chiave"]] = voce_reg
        if chunk_id not in voce_reg["brani"]:
            voce_reg["brani"].append(chunk_id)
        if voce_reg["stato"] != DECISA and decide is not None and not voce_reg.get("chiesta"):
            domande += 1
            voce_reg["chiesta"] = True
            domanda = {
                "chiave": voce["chiave"],
                "originale": voce_reg["originale"],
                "contesto": voce_reg["contesto"],
                "tipo": voce_reg["tipo"],
                "opzioni": voce_reg["opzioni"],
                "lingua": target_language,
                "book_pos": book_pos,
            }
            try:
                risposta = decide(domanda)
            except Exception as exc:  # noqa: BLE001 - una finestra che si chiude non ferma il libro
                voce_reg["errore"] = str(exc)
                risposta = None
            scelta = interpreta_scelta(voce_reg, risposta)
            if scelta:
                voce_reg.update(scelta, stato=DECISA)
        if voce_reg["stato"] == DECISA and voce_reg.get("resa"):
            scelte.append({"originale": voce_reg["originale"], "resa": voce_reg["resa"]})
    return scelte, domande


def testo_delle_scelte(scelte: list[dict]) -> str:
    """Le rese decise dall'utente, come vincolo per il traduttore."""
    if not scelte:
        return ""
    righe = "; ".join(f"«{s['originale']}» → «{s['resa']}»" for s in scelte)
    return (
        "Rese decise dall'utente per modi di dire e riferimenti culturali (VINCOLANTI: usa "
        f"esattamente queste, adattando solo la concordanza grammaticale): {righe}"
    )


# --------------------------------------------------------------------------- #
#  rifinitura madrelingua
# --------------------------------------------------------------------------- #
RIFINITURA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"rifinito": {"type": "string"}},
    "required": ["rifinito"],
}


def _misura(testo: str) -> int:
    """Lunghezza senza spazi: vale anche per le lingue che non separano le parole."""
    return len(re.sub(r"\s+", "", testo or ""))


def _paragrafi_conteggio(testo: str) -> int:
    return len(_paragrafi(testo))


def rifinisci(
    testo: str,
    *,
    originale: str,
    target_language: str | None,
    vincolati: list[str],
    client: Any,
    model: str | None,
    conserva_paragrafi: bool,
) -> tuple[str, str]:
    """Passata di un editor madrelingua sulla traduzione. Ritorna ``(testo, esito)``.

    ``esito`` comincia con «applicata», «scartata» o «non riuscita»: nei due ultimi casi il testo
    restituito è quello di prima. Lo si scarta quando la lunghezza cambia oltre il 15%, quando
    cambiano i titoli o (se l'impaginazione va conservata) il numero dei paragrafi, e quando
    sparisce un termine del glossario o una resa decisa dall'utente: una rifinitura che fa una
    di queste cose non sta più rifinendo.
    """
    presenti = sorted({v for v in vincolati if v and v in testo})
    if target_language:
        apertura = (
            f"Sei un editor di madrelingua {target_language} che rivede una traduzione "
            f"letteraria. Rendila come l'avrebbe scritta un autore di madrelingua "
            f"{target_language}: correggi i calchi dalla lingua di partenza, la sintassi "
            "innaturale, i falsi amici (confronta con l'ORIGINALE), le collocazioni e il ritmo "
            "goffi."
        )
    else:
        # Riscrittura nella stessa lingua: l'editor è madrelingua della lingua del testo.
        apertura = (
            "Sei un editor di madrelingua della lingua in cui è scritto il testo indicato come "
            "TRADUZIONE (qui: la versione rielaborata dell'ORIGINALE), e lo rivedi perché suoni "
            "naturale a un lettore nativo: correggi sintassi "
            "innaturale, collocazioni e ritmo goffi, forme rimaste meccaniche."
        )
    system = (
        apertura + " NON cambiare il significato, i fatti, i nomi, i "
        "numeri, il registro e il tono; non aggiungere né togliere contenuto, non aggiungere "
        "note. Conserva la divisione in paragrafi (stessi paragrafi, nello stesso ordine, "
        "separati da una riga vuota); i titoli coi cancelletti (## …) restano titoli. Se una "
        "frase è già naturale lasciala com'è."
    )
    if presenti:
        system += " Questi termini e queste rese devono restare IDENTICI: " + "; ".join(
            f"«{v}»" for v in presenti
        ) + "."
    system += " Rispondi in JSON: 'rifinito' (il testo rivisto, per intero)."
    try:
        risposta = client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"ORIGINALE:\n{originale}\n\nTRADUZIONE:\n{testo}"},
            ],
            schema=RIFINITURA_SCHEMA,
            model=model,
        )
    except LLMError as exc:
        return testo, f"non riuscita: {exc}"
    rifinito = str((risposta or {}).get("rifinito") or "").strip()
    if not rifinito:
        return testo, "scartata: risposta vuota"
    prima, dopo = _misura(testo), _misura(rifinito)
    if prima and abs(dopo - prima) / prima > TOLLERANZA_RIFINITURA:
        return testo, f"scartata: lunghezza cambiata del {round(100 * (dopo - prima) / prima):+d}%"
    if conta_titoli(rifinito) != conta_titoli(testo):
        return testo, "scartata: titoli cambiati"
    if conserva_paragrafi and _paragrafi_conteggio(rifinito) != _paragrafi_conteggio(testo):
        return testo, (
            f"scartata: paragrafi da {_paragrafi_conteggio(testo)} a "
            f"{_paragrafi_conteggio(rifinito)}"
        )
    persi = [v for v in presenti if v not in rifinito]
    if persi:
        return testo, "scartata: cambiati termini fissati (" + ", ".join(persi[:3]) + ")"
    if rifinito == testo:
        return testo, "applicata: nessuna modifica necessaria"
    return rifinito, "applicata"
