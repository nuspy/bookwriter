"""Aggiornare un manoscritto: un cambiamento seguito in tutto il libro, non sostituito in un punto.

Spostare un evento dall'Empire State Building alla Torre di Pisa non è cambiare un nome. Cambiano
il viaggio (voli, biglietti, il percorso dall'aeroporto), i luoghi attraversati e le persone
incontrate, la lingua, la moneta e la mancia al cameriere (il 20% a New York, qualche euro in
Italia), il cibo, le leggi, il lato della strada su cui si guida, le superstizioni (il piano 13
che negli alberghi americani non c'è, i piani col 4 che a Hong Kong si saltano), il clima e la
stagione — gennaio è inverno a nord dell'equatore ed estate a sud —, il meteo credibile del
periodo, la luce, i suoni, l'atmosfera. Un aggiornamento credibile li trova tutti, li documenta e
li scrive nella voce del libro.

Il lavoro, in quattro tempi:

1. **il profilo delle differenze** (:func:`profile`): per ogni cambiamento deciso con l'autore, le
   categorie in cui il vecchio e il nuovo si distinguono, ciascuna con la domanda da documentare
   e le parole con cui il libro può nominare il vecchio;
2. **la mappa dell'impatto** (:func:`scan`): ogni capitolo letto per intero, paragrafo per
   paragrafo, con il profilo accanto — anche gli effetti indiretti, che nessuna parola chiave
   trova (una stagione, un tragitto, un'usanza); i capitoli toccati diventano «da aggiornare», e
   le loro domande diventano i punti della ricerca;
3. **l'applicazione** (:func:`apply_chapter`): solo i paragrafi toccati si riscrivono, con il
   materiale trovato dalla ricerca e la voce del libro; il resto resta parola per parola;
4. **i residui** (:func:`residual_sweep`) e il **documento delle modifiche**
   (:func:`write_outputs`): una seconda passata cerca nel libro aggiornato i rimandi rimasti al
   vecchio, e il libro esce anche con ogni modifica segnata, capitolo per capitolo, col perché.

Fra il profilo e la mappa c'è **la catena delle dipendenze** (:func:`dependencies`): un fatto
spostato nel capitolo 4 cambia chi lo sa nei capitoli dal 5 al 12, gli eventi che ne seguivano, i
tempi di viaggio, le scadenze. Il profilo li nomina solo se ci pensa; il registro del racconto
(:mod:`bookwriter.forge.ledger`: chi sa cosa, eventi, oggetti, luoghi, tempi) e le cronologie
delle scene (:mod:`bookwriter.forge.timeline`) li sanno. Dal registro si ricava il grafo di quello
che dipende dal fatto cambiato — dove il fatto si stabilisce, chi lo viene a sapere, quando e da
chi, cosa ne segue — e ogni capitolo che ne dipende si legge sapendo cosa cercarvi: ne escono
impatti come gli altri, con il loro «dipende da: …». Finito l'aggiornamento, la cronologia dei
capitoli toccati si **ricostruisce** sul testo nuovo (:func:`consistency_check`): orari in
ordine, spostamenti mostrati, scadenze con un'ora sola; quello che non torna si riguarda una
volta.

Due categorie hanno un nome fisso, e il profilo, la lettura e il documento delle modifiche le
considerano sempre: **personaggi** (chi è coinvolto, le relazioni, cosa sa ciascuno) e **dialoghi**
(le battute che nominano o presuppongono il vecchio, il modo di parlare che deve cambiare).

Tutto sta in ``story/impact.json``; l'originale resta intatto in ``originale/``.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from bookwriter.forge import notebook as nb
from bookwriter.forge.context import ContextPack
from bookwriter.forge.metrics import content_words, count_words
from bookwriter.forge.project import BookProject, ForgeState

ORIGINAL, TO_UPDATE, UPDATED = "originale", "da_aggiornare", "aggiornato"

#: Le due categorie con un nome fisso: il profilo e la lettura le considerano sempre, il documento
#: delle modifiche si raggruppa per loro.
CHARACTERS, DIALOGUES = "personaggi", "dialoghi"
NAMED_CATEGORIES: dict[str, str] = {
    CHARACTERS: "personaggi (chi è coinvolto, le relazioni, cosa sa ciascuno, da quando e da chi "
                "lo ha saputo)",
    DIALOGUES: "dialoghi (le battute che nominano o presuppongono il vecchio, il modo di parlare "
               "che deve cambiare: lingua, formule, titoli, accento)",
}
#: Le categorie degli impatti che non vengono dal profilo: la catena delle dipendenze, la
#: cronologia ricostruita dopo le modifiche, le parole del vecchio trovate o rimaste.
DEPENDENCY, CHRONOLOGY = "dipendenza", "cronologia"

#: I tipi di anello della catena delle dipendenze.
DEPENDENCY_KINDS = ("evento", "conoscenza", "relazione", "oggetto", "luogo", "viaggio", "tempo",
                    "scadenza", "dialogo")
#: Quanti anelli per cambiamento chiede la ricerca delle dipendenze: l'uscita resta breve.
MAX_LINKS = 24
#: Quanti anelli ricavati dalle sole parole (senza modello) per capitolo e cambiamento.
MAX_RULE_LINKS = 4

#: Le categorie in cui due luoghi, due epoche o due ambienti si distinguono. Sono la lista da cui
#: il profilo parte: il modello ne aggiunge altre se il cambiamento lo chiede.
CATEGORIES = (
    NAMED_CATEGORIES[CHARACTERS],
    NAMED_CATEGORIES[DIALOGUES],
    "viaggio e trasporti (aeroporti, voli, treni, percorsi, durate, documenti e visti)",
    "luoghi e toponimi (vie, quartieri, edifici, panorami, distanze)",
    "persone incontrate (mestieri, nomi tipici, lingua, modi)",
    "lingua e saluti (formule di cortesia, titoli, gesti)",
    "moneta, prezzi e mance (quanto, come, a chi)",
    "cibo e bevande (piatti, orari dei pasti, locali, abitudini)",
    "usanze, superstizioni e numeri (piani degli edifici, numeri sfortunati, festività)",
    "leggi e regole pratiche (alcol, fumo, guida, età, documenti, sicurezza)",
    "guida e traffico (lato della strada, mezzi, taxi, segnaletica)",
    "unità di misura, prese elettriche, formati di data e ora",
    "clima, stagione ed emisfero, meteo credibile nel periodo, ore di luce",
    "marche, negozi, giornali, istituzioni e servizi (polizia, numero di emergenza, sanità)",
    "atmosfera sensoriale (suoni, odori, luce, folla, ritmo della città)",
    "storia e fatti del luogo che il libro cita",
)

_T = {"type": "string"}
_L = {"type": "array", "items": _T}

PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"changes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "change": _T, "kind": _T, "old": _T, "new": _T, "when": _T,
            "facets": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"category": _T, "old": _T, "question": _T, "old_terms": _L},
                "required": ["category", "old", "question", "old_terms"]}}},
        "required": ["change", "kind", "old", "new", "when", "facets"]}}},
    "required": ["changes"],
}

SCAN_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"impacts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"paragraphs": {"type": "array", "items": {"type": "integer"}},
                       "change": {"type": "integer"}, "category": _T, "what": _T, "need": _T,
                       "action": _T, "depends": _L},
        "required": ["paragraphs", "change", "category", "what", "need", "action", "depends"]}}},
    "required": ["impacts"],
}

DEPENDENCY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"links": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"chapter": {"type": "integer"},
                       "kind": {"type": "string", "enum": list(DEPENDENCY_KINDS)},
                       "fact": _T, "who": _L, "depends_on_chapter": {"type": "integer"},
                       "depends_on": _T, "why": _T},
        "required": ["chapter", "kind", "fact", "who", "depends_on_chapter", "depends_on",
                     "why"]}}},
    "required": ["links"],
}

APPLY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"rewritten": _L, "insert_after": _T},
    "required": ["rewritten", "insert_after"],
}


# --------------------------------------------------------------------------- #
#  su disco
# --------------------------------------------------------------------------- #
def impact_path(project: BookProject) -> Path:
    return project.story_dir / "impact.json"


def _empty() -> dict[str, Any]:
    """Il contenuto di ``impact.json`` quando non c'è ancora niente.

    - ``profile``: il profilo delle differenze, un elemento per cambiamento;
    - ``chapters``: numero del capitolo → impatti da applicare (``depends`` = gli anelli della
      catena da cui l'impatto nasce, se ce ne sono);
    - ``applied``: numero → cosa l'ultima applicazione ha fatto (``items``: categoria e motivo
      di ogni punto); ``applied_history``: le applicazioni dei giri prima (residui, cronologia);
    - ``residual``: le parole del vecchio rimaste;
    - ``dependencies``: gli anelli della catena (vedi :func:`dependencies`);
      ``dependency_errors``: le ricerche delle dipendenze non riuscite;
    - ``consistency``: la cronologia ricostruita dopo le modifiche (vedi
      :func:`consistency_check`).
    """
    return {"profile": [], "chapters": {}, "applied": {}, "residual": [], "dependencies": [],
            "dependency_errors": [], "consistency": {}, "applied_history": {}}


def load(project: BookProject) -> dict[str, Any]:
    path = impact_path(project)
    if not path.exists():
        return _empty()
    try:
        dati = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(dati, dict):
        return _empty()
    for k, v in _empty().items():
        dati.setdefault(k, v)
    return dati


def save(project: BookProject, dati: dict[str, Any]) -> Path:
    path = impact_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
#  1. il profilo delle differenze
# --------------------------------------------------------------------------- #
def profile(state: ForgeState, client: Any, summary: str, *, model: str | None = None
            ) -> list[dict[str, Any]]:
    """Per ogni cambiamento deciso, in cosa il vecchio e il nuovo si distinguono.

    Non afferma i fatti del luogo nuovo: li chiede. Ogni categoria ha la domanda che la ricerca
    deve documentare («Negli alberghi di Hong Kong quali piani mancano, e perché?») — la risposta
    la danno le fonti, non il modello che progetta.
    """
    cambiamenti = nb.normalise(state.notebook).get("changes") or []
    if not cambiamenti:
        return []
    categorie = "\n".join(f"- {c}" for c in CATEGORIES)
    sistema = (
        "Sei il consulente di un romanziere che deve spostare il suo libro da una realtà a "
        "un'altra (un luogo, un'epoca, un ambiente, un evento). Per ogni cambiamento, elenca le "
        "differenze che un lettore del posto noterebbe se il libro sbagliasse: non solo le "
        "ovvie, soprattutto i dettagli culturali e pratici che danno credibilità — le "
        "superstizioni sui numeri (i piani che negli alberghi mancano), la mancia e quanto, il "
        "lato della strada su cui si guida, la moneta, gli orari dei pasti, le formule di "
        "saluto, le leggi pratiche, il clima e la stagione nell'emisfero giusto, il meteo "
        "credibile nel periodo, le ore di luce, le festività, i mezzi di trasporto, i percorsi "
        "reali.\n\n"
        f"Parti da queste categorie e aggiungine se servono:\n{categorie}\n\n"
        f"Considera SEMPRE anche le categorie '{CHARACTERS}' e '{DIALOGUES}', con proprio questi "
        f"nomi in 'category': '{CHARACTERS}' = chi nel libro è coinvolto nel vecchio, con quali "
        "relazioni, cosa ne sa ciascuno e da chi lo ha saputo (un fatto spostato cambia chi lo "
        f"sa e quando); '{DIALOGUES}' = le battute che nominano o presuppongono il vecchio e il "
        "modo di parlare che deve cambiare (lingua, formule, titoli, accento). Per queste due "
        "la 'question' è che cosa controllare nel libro stesso.\n\n"
        "Per ogni differenza: 'category'; 'old' = come appare nel libro di adesso; 'question' "
        "= la domanda precisa da documentare sul NUOVO (non la risposta: la risposta la "
        "daranno le fonti); 'old_terms' = le parole con cui il libro può nominare il vecchio "
        "(nomi di luoghi, vie, monete, marche, istituzioni), per ritrovarle nel testo. Per "
        "ogni cambiamento indica anche 'when': il periodo dell'anno in cui il libro lo colloca, "
        "se si capisce dal riassunto (serve per clima e meteo). Da 8 a 20 differenze per "
        f"cambiamento. Scrivi in {nb.language_name(state)}."
    )
    utente = ("# I cambiamenti decisi con l'autore\n" + "\n".join(f"{i + 1}. {c}" for i, c in
                                                             enumerate(cambiamenti))
              + f"\n\n# Il libro, capitolo per capitolo\n{summary}\n\n"
              f"# Il quaderno\n{nb.brief(state)}")
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=PROFILE_SCHEMA, model=model)
    except Exception:  # noqa: BLE001 - senza profilo la scansione lavora sui soli cambiamenti
        r = {}
    fuori = []
    for i, c in enumerate((r.get("changes") or []) if isinstance(r, dict) else []):
        if not isinstance(c, dict):
            continue
        fuori.append({"n": i + 1, "change": str(c.get("change") or cambiamenti[min(
            i, len(cambiamenti) - 1)]), "kind": str(c.get("kind") or ""),
            "old": str(c.get("old") or ""), "new": str(c.get("new") or ""),
            "when": str(c.get("when") or ""),
            "facets": [{"category": category_key(str(f.get("category") or "")),
                        "old": str(f.get("old") or ""),
                        "question": str(f.get("question") or ""),
                        "old_terms": [str(t) for t in f.get("old_terms") or [] if str(t).strip()]}
                       for f in c.get("facets") or [] if isinstance(f, dict)]})
    if not fuori:
        fuori = [{"n": i + 1, "change": c, "kind": "", "old": "", "new": "", "when": "",
                  "facets": []} for i, c in enumerate(cambiamenti)]
    for c in fuori:
        _ensure_named_facets(c)
    return fuori


def category_key(nome: str) -> str:
    """Il nome di una categoria, con 'personaggi' e 'dialoghi' riconosciuti comunque il modello
    li scriva («Personaggi e relazioni», «dialogue», «battute»…); le altre restano come sono."""
    testo = (nome or "").strip()
    t = testo.lower()
    if re.search(r"dialog|battut|conversaz", t):
        return DIALOGUES
    if re.search(r"personagg|character|relazion|chi sa\b|conoscenz", t) and \
            "incontrat" not in t:
        return CHARACTERS
    return testo


def _ensure_named_facets(cambiamento: dict[str, Any]) -> None:
    """Ogni cambiamento ha le sue differenze di personaggi e di dialoghi: se il modello non le ha
    scritte, ci sono comunque, come domanda da controllare nel libro."""
    vecchio = cambiamento.get("old") or cambiamento.get("change") or ""
    presenti = {f.get("category") for f in cambiamento.get("facets") or []}
    domande = {
        CHARACTERS: f"Chi, nel libro, è coinvolto in «{vecchio}» o ne sa qualcosa, con quali "
                    "relazioni, da quando e da chi lo ha saputo? Cosa cambia per ciascuno?",
        DIALOGUES: f"Quali battute nominano o presuppongono «{vecchio}», e quale modo di parlare "
                   "(lingua, formule, titoli, accento) deve cambiare?",
    }
    for chiave in (CHARACTERS, DIALOGUES):
        if chiave not in presenti:
            cambiamento.setdefault("facets", []).append(
                {"category": chiave, "old": "", "question": domande[chiave], "old_terms": []})


def _profile_text(profilo: list[dict[str, Any]]) -> str:
    righe = []
    for c in profilo:
        righe.append(f"## Cambiamento {c['n']}: {c['change']}")
        if c.get("old") or c.get("new"):
            righe.append(f"Da: {c.get('old')} → A: {c.get('new')}"
                         + (f" (periodo: {c['when']})" if c.get("when") else ""))
        for f in c.get("facets") or []:
            termini = ", ".join(f.get("old_terms") or [])
            interno = f.get("category") in NAMED_CATEGORIES
            righe.append(f"- {f['category']}: " + (f"nel libro «{f['old']}» → " if f.get("old")
                                                   else "")
                         + ("da controllare nel libro: " if interno else "da documentare: ")
                         + f"{f['question']}" + (f" [parole: {termini}]" if termini else ""))
    return "\n".join(righe)


# --------------------------------------------------------------------------- #
#  1b. la catena delle dipendenze
# --------------------------------------------------------------------------- #
def _words(testo: str) -> set[str]:
    return {w for w in content_words(testo or "") if len(w) > 3}


def _term_re(termine: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(termine)}(?!\w)", re.IGNORECASE)


def _name_parts(chi: list[str]) -> list[str]:
    """I pezzi dei nomi con cui il testo può chiamare un personaggio («Marta Rossi» → Marta,
    Rossi), senza le parole comuni («il lettore», «il protagonista»)."""
    parti = []
    for nome in chi or []:
        for pezzo in re.findall(r"\w+", str(nome)):
            if len(pezzo) >= 3 and pezzo[:1].isupper():
                parti.append(pezzo)
    return list(dict.fromkeys(parti))


def _old_terms(cambiamento: dict[str, Any]) -> list[str]:
    """Le parole con cui il libro nomina il vecchio: quelle del profilo e il vecchio stesso, se
    è un nome breve."""
    termini = [str(t).strip() for f in cambiamento.get("facets") or []
               for t in f.get("old_terms") or []]
    vecchio = str(cambiamento.get("old") or "").strip()
    if vecchio and len(vecchio) <= 40:
        termini.append(vecchio)
    return list(dict.fromkeys(t for t in termini if len(t) >= 3))


def _knowledge_text(k: dict[str, str]) -> str:
    fonte = f" (da {k['source']})" if k.get("source") else ""
    return f"{k.get('who') or '?'} viene a sapere: {k.get('what')}{fonte}"


def _chapter_facts(voce: Any, cronologia: Any, protagonista: str
                   ) -> list[tuple[str, str, list[str]]]:
    """I fatti che il registro e la cronologia delle scene sanno di un capitolo, come
    (tipo d'anello, testo, chi)."""
    fatti: list[tuple[str, str, list[str]]] = []
    io = [protagonista] if protagonista else []
    if voce is not None and not voce.error:
        stato = voce.end_state
        if stato.place:
            ora = f", {stato.time}" if stato.time else ""
            fatti.append(("luogo", f"alla fine è a {stato.place}{ora}", io))
        for e in getattr(voce, "events", None) or []:
            dove = f"{e.get('when') or '?'}, {e.get('where') or '?'}"
            fatti.append(("evento", f"{dove}: {e.get('what')}", []))
        for k in getattr(voce, "knowledge", None) or []:
            fatti.append(("conoscenza", _knowledge_text(k), [k["who"]] if k.get("who") else []))
        fatti += [("conoscenza", f"il protagonista sa: {x}", io) for x in stato.knows]
        fatti += [("conoscenza", f"detto al lettore: {x}", []) for x in voce.told]
        fatti += [("oggetto", f"ha con sé: {x}", io) for x in stato.objects]
        fatti += [("relazione", str(x), []) for x in stato.relations]
    for r in getattr(cronologia, "records", None) or []:
        if r.error:
            continue
        quando = ", ".join(x for x in (r.day, r.time_start) if x) or "?"
        for e in r.events:
            dove = f"{e.get('time') or quando}, {r.place_start or '?'}"
            fatti.append(("evento", f"{dove}: {e.get('what')}", list(r.present[:4])))
        for k in r.knowledge:
            fatti.append(("conoscenza", _knowledge_text(k), [k["who"]] if k.get("who") else []))
        for o in r.objects:
            stato_oggetto = f"{o.get('status')}, ha: {o.get('holder') or '?'}"
            fatti.append(("oggetto", f"{o.get('what')} ({stato_oggetto})",
                          [o["holder"]] if o.get("holder") else []))
        for sc in r.deadlines:
            fatti.append(("scadenza", f"{sc.get('what')}: {sc.get('when')}", []))
    return fatti


def book_register(project: BookProject, state: ForgeState) -> dict[int, dict[str, Any]]:
    """Il libro come registro, capitolo per capitolo: titolo, riassunto e fatti (chi sa cosa e da
    chi, eventi, oggetti, luoghi, tempi, scadenze) dal registro del racconto e dalle cronologie
    delle scene, dove ci sono. È quello che la ricerca delle dipendenze legge."""
    from bookwriter.forge import ledger as lg
    from bookwriter.forge import timeline as tl

    registro = lg.load(project)
    scritti = project.written_chapters()
    protagonista = nb.protagonist(state)
    fuori: dict[int, dict[str, Any]] = {}
    for c in state.outline:
        n = int(c.get("number") or 0)
        if n not in scritti:
            continue
        titolo = str(c.get("title") or "")
        voce = registro.entry(n)
        cronologia = tl.load(tl.timeline_path(project, n, titolo))
        fuori[n] = {"title": titolo,
                    "summary": (voce.summary if voce is not None and not voce.error else "")
                    or str(c.get("does") or ""),
                    "facts": _chapter_facts(voce, cronologia, protagonista)}
    return fuori


def _register_text(registri: dict[int, dict[str, Any]], tetto: int = 1600) -> str:
    blocchi = []
    for n in sorted(registri):
        r = registri[n]
        righe = [f"## Capitolo {n}: {r['title']}"]
        if r["summary"]:
            righe.append(f"Riassunto: {r['summary']}")
        righe += [f"- ({tipo}) {testo}" for tipo, testo, _chi in r["facts"]]
        testo = "\n".join(righe)
        blocchi.append(testo if len(testo) <= tetto else testo[:tetto].rsplit("\n", 1)[0])
    return "\n\n".join(blocchi)


def _kind(valore: str) -> str:
    v = (valore or "").strip().lower()
    if v in DEPENDENCY_KINDS:
        return v
    for k in DEPENDENCY_KINDS:
        if k[:5] in v:
            return k
    return "evento"


def _node_label(nodo: dict[str, Any]) -> str:
    chi = f" ({', '.join(nodo['who'])})" if nodo.get("who") else ""
    return f"cap. {nodo['chapter']} · {nodo['kind']}{chi}: {nodo['fact']}"


def _overlap(a: str, b: str) -> int:
    return len(_words(a) & _words(b))


def _same_fact(nodi: list[dict[str, Any]], capitolo: int, fatto: str) -> dict[str, Any] | None:
    """Un anello già nel grafo che dice lo stesso fatto, nello stesso capitolo."""
    parole = _words(fatto)
    for d in nodi:
        if d["chapter"] != capitolo:
            continue
        altre = _words(d["fact"])
        if parole and altre and len(parole & altre) >= 0.6 * min(len(parole), len(altre)):
            return d
    return None


class _Chain:
    """Il grafo di un cambiamento: ogni anello sa da quale dipende (``parent``; vuoto = dal
    cambiamento stesso), a che profondità sta e la catena dal cambiamento a lui."""

    def __init__(self, cambiamento: dict[str, Any]) -> None:
        self.n = int(cambiamento.get("n") or 0)
        self.radice = f"Cambiamento {self.n}: {cambiamento.get('change')}"
        self.nodi: list[dict[str, Any]] = []

    def add(self, capitolo: int, tipo: str, fatto: str, chi: list[str],
            padre: dict[str, Any] | None, perche: str, origine: str, **extra: Any
            ) -> dict[str, Any]:
        nodo = {"id": f"{self.n}.{len(self.nodi) + 1}", "change": self.n, "chapter": capitolo,
                "kind": tipo, "fact": fatto.strip()[:300],
                "who": [str(x).strip() for x in chi if str(x).strip()][:6],
                "parent": padre["id"] if padre else "", "because": perche.strip()[:300],
                "origin": origine, "depth": (padre["depth"] + 1) if padre else 1, **extra}
        nodo["chain"] = (padre["chain"] if padre else [self.radice]) + [_node_label(nodo)]
        self.nodi.append(nodo)
        return nodo

    def parent_for(self, capitolo: int, fonte: int, da_cosa: str) -> dict[str, Any] | None:
        """L'anello da cui dipende un fatto del capitolo ``capitolo`` che il modello dice
        dipendere da ``da_cosa`` nel capitolo ``fonte``; se il grafo non ce l'ha, lo aggiunge."""
        if fonte <= 0 or fonte > capitolo:
            return None
        candidati = [d for d in self.nodi if d["chapter"] == fonte]
        if candidati:
            migliore = max(candidati, key=lambda d: _overlap(da_cosa, d["fact"] + " "
                                                             + " ".join(d["who"])))
            if _overlap(da_cosa, migliore["fact"] + " " + " ".join(migliore["who"])) > 0 or \
                    len(candidati) == 1:
                return migliore
        if not da_cosa.strip():
            return candidati[0] if candidati else None
        return self.add(fonte, "evento", da_cosa, [], None,
                        f"il fatto da cui dipende il capitolo {capitolo}", "modello")


def _links_from_model(state: ForgeState, client: Any, cambiamento: dict[str, Any],
                      testo_registro: str, scritti: set[int], *, model: str | None
                      ) -> list[dict[str, Any]]:
    """Gli anelli che il modello trova leggendo il registro del libro. Solleva se la chiamata
    non riesce: chi chiama lo annota."""
    sistema = (
        "Sei il curatore della continuità di un romanzo. L'autore ha deciso il cambiamento qui "
        "sotto. Leggi il registro del libro, capitolo per capitolo (chi sa cosa e da chi, eventi "
        "con quando e dove, oggetti, luoghi, relazioni) e trova la CATENA delle conseguenze: "
        "dove il fatto cambiato si stabilisce, e poi ogni fatto dei capitoli che ne dipende — "
        "gli eventi successivi che ne seguivano, chi lo viene a sapere (quando, da chi) e cosa "
        "fa perché lo sa, le relazioni, gli oggetti, i luoghi, i viaggi e i loro tempi, le "
        "scadenze, le battute che lo presuppongono.\n\n"
        "Per ogni anello: 'chapter' = il numero del capitolo dove sta il fatto; 'kind' = uno fra "
        f"{', '.join(DEPENDENCY_KINDS)}; 'fact' = il fatto di quel capitolo che dipende dal "
        "cambiamento, in una riga concreta; 'who' = i personaggi coinvolti; "
        "'depends_on_chapter' = il capitolo del fatto da cui questo dipende (0 se dipende "
        "direttamente dal cambiamento); 'depends_on' = quel fatto, in poche parole; 'why' = "
        "perché il cambiamento lo tocca, in una riga. Solo dipendenze che il registro mostra: "
        "non inventare per riempire; un capitolo che non ne dipende non compare. Al massimo "
        f"{MAX_LINKS} anelli, in ordine di capitolo. Scrivi in {nb.language_name(state)}."
    )
    utente = (f"# Il cambiamento\n{_profile_text([cambiamento])}\n\n"
              f"# Il registro del libro\n{testo_registro}")
    r = client.chat_json([{"role": "system", "content": sistema},
                          {"role": "user", "content": utente}],
                         schema=DEPENDENCY_SCHEMA, model=model)
    fuori = []
    for x in ((r.get("links") or []) if isinstance(r, dict) else [])[:MAX_LINKS]:
        if not isinstance(x, dict) or not str(x.get("fact") or "").strip():
            continue
        try:
            capitolo = int(x.get("chapter") or 0)
            fonte = int(x.get("depends_on_chapter") or 0)
        except (TypeError, ValueError):
            continue
        if capitolo not in scritti:
            continue
        chi = x.get("who") or []
        fuori.append({"chapter": capitolo, "kind": _kind(str(x.get("kind") or "")),
                      "fact": str(x["fact"]).strip(),
                      "who": [str(w) for w in (chi if isinstance(chi, list) else [chi])],
                      "source": fonte if fonte in scritti else 0,
                      "depends_on": str(x.get("depends_on") or "").strip(),
                      "why": str(x.get("why") or "").strip()})
    return sorted(fuori, key=lambda x: x["chapter"])


def _rule_links(catena: _Chain, cambiamento: dict[str, Any],
                registri: dict[int, dict[str, Any]]) -> None:
    """Gli anelli che si vedono senza modello: i fatti del registro che nominano il vecchio."""
    termini = [(t, _term_re(t)) for t in _old_terms(cambiamento)]
    if not termini:
        return
    for n in sorted(registri):
        aggiunti = 0
        for tipo, testo, chi in registri[n]["facts"]:
            if aggiunti >= MAX_RULE_LINKS:
                break
            termine = next((t for t, schema in termini if schema.search(testo)), "")
            if not termine or _same_fact(catena.nodi, n, testo):
                continue
            catena.add(n, tipo, testo, chi, None, f"il registro nomina «{termine}»", "regola",
                       term=termine)
            aggiunti += 1


def _propagate_knowledge(catena: _Chain, registri: dict[int, dict[str, Any]]) -> None:
    """Chi viene a sapere il fatto lo porta con sé: nei capitoli dopo in cui quel personaggio
    agisce su quello che sa (il registro lo nomina e parla della stessa cosa), il capitolo ne
    dipende. Senza modello, con prudenza: nome del personaggio e almeno tre parole in comune."""
    for nodo in list(catena.nodi):
        if nodo["kind"] != "conoscenza":
            continue
        for nome in _name_parts(nodo["who"]):
            schema = _term_re(nome)
            ultimo = nodo
            for j in sorted(registri):
                if j <= nodo["chapter"]:
                    continue
                gia = next((d for d in catena.nodi if d["chapter"] == j
                            and any(nome in w for w in d["who"])), None)
                if gia is not None:  # il capitolo è già nella catena per lui: si prosegue da lì
                    ultimo = gia
                    continue
                r = registri[j]
                testo = " ".join([r["summary"], *(t for _k, t, _c in r["facts"])])
                if not schema.search(testo) or _overlap(nodo["fact"], testo) < 3:
                    continue
                ultimo = catena.add(j, "conoscenza", f"{nome} agisce sapendo: {nodo['fact']}",
                                    [nome], ultimo,
                                    f"{nome} lo ha saputo nel capitolo {nodo['chapter']}",
                                    "regola")


def dependencies(project: BookProject, state: ForgeState, client: Any,
                 profilo: list[dict[str, Any]], *, model: str | None = None,
                 should_stop=None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """La catena delle dipendenze di ogni cambiamento, dal registro del libro.

    Ritorna (anelli, errori). Ogni anello: ``id`` («1.3»), ``change`` (il numero del
    cambiamento), ``chapter``, ``kind`` (uno di :data:`DEPENDENCY_KINDS`), ``fact`` (il fatto di
    quel capitolo che dipende), ``who`` (i personaggi), ``parent`` (l'id dell'anello da cui
    dipende; vuoto = dal cambiamento stesso), ``because``, ``origin`` (``modello`` o ``regola``),
    ``depth`` e ``chain`` (le etichette dal cambiamento all'anello). Una ricerca che non riesce
    si annota negli errori e il lavoro continua con quello che si vede senza modello.
    """
    if not profilo:
        return [], []
    registri = book_register(project, state)
    if not registri:
        return [], [{"change": 0, "error": "nessun capitolo scritto: dipendenze non cercate"}]
    testo_registro = _register_text(registri)
    vuoto = not any(r["facts"] for r in registri.values())
    nodi: list[dict[str, Any]] = []
    errori: list[dict[str, Any]] = []
    for cambiamento in profilo:
        if should_stop and should_stop():
            break
        catena = _Chain(cambiamento)
        if vuoto:
            errori.append({"change": catena.n, "error": "il registro del racconto è vuoto: "
                           "ricavalo dai capitoli per seguire le dipendenze"})
        elif client is not None:
            try:
                anelli = _links_from_model(state, client, cambiamento, testo_registro,
                                           set(registri), model=model)
            except Exception as exc:  # noqa: BLE001 - restano gli anelli che si vedono da soli
                errori.append({"change": catena.n, "error": str(exc)[:200]})
                anelli = []
            for a in anelli:
                if _same_fact(catena.nodi, a["chapter"], a["fact"]):
                    continue
                padre = catena.parent_for(a["chapter"], a["source"], a["depends_on"])
                catena.add(a["chapter"], a["kind"], a["fact"], a["who"], padre, a["why"],
                           "modello")
        _rule_links(catena, cambiamento, registri)
        _propagate_knowledge(catena, registri)
        nodi += catena.nodi
    return nodi, errori


def _kind_category(tipo: str) -> str:
    """La categoria dell'impatto che nasce da un anello della catena."""
    if tipo in ("conoscenza", "relazione"):
        return CHARACTERS
    if tipo == "dialogo":
        return DIALOGUES
    return DEPENDENCY


def dependency_reason(nodo: dict[str, Any]) -> str:
    """Da cosa dipende un anello, in una riga: l'anello prima nella catena e il perché."""
    catena = nodo.get("chain") or []
    # un anello attaccato al cambiamento stesso: basta il numero, il testo è già nel piano
    da = catena[-2] if len(catena) >= 3 else f"cambiamento {nodo.get('change')}"
    return da + (f" ({nodo['because']})" if nodo.get("because") else "")


def _locate(blocchi: list[str], nodo: dict[str, Any]) -> list[int]:
    """Il paragrafo in cui un anello sta, se lo si vede con sicurezza: parole del fatto in comune
    e il nome di chi è coinvolto."""
    parole = _words(nodo.get("fact") or "")
    nomi = [_term_re(n) for n in _name_parts(nodo.get("who") or [])]
    migliore, punti = 0, 0
    for i, b in enumerate(blocchi):
        p = len(parole & _words(b)) + (2 if any(s.search(b) for s in nomi) else 0)
        if p > punti:
            migliore, punti = i + 1, p
    return [migliore] if punti >= 3 else []


def _dependency_entries(blocchi: list[str], anelli: list[dict[str, Any]],
                        impatti: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gli anelli di un capitolo che la lettura non ha legato a un punto: diventano impatti
    anch'essi, con il loro «dipende da». Quelli del modello si cercano nel testo; se non si
    trovano, o se vengono dalle sole parole del registro, restano «verifica» — visibili nel piano
    e nel documento delle modifiche, senza riscrivere a caso."""
    coperti = {i for imp in impatti for i in imp.get("depends") or []}
    fuori = []
    for d in anelli:
        if d["id"] in coperti:
            continue
        paragrafi = _locate(blocchi, d) if d.get("origin") == "modello" else []
        fuori.append({"paragraphs": paragrafi, "change": d["change"],
                      "category": _kind_category(d["kind"]),
                      "what": f"{d['fact']} — dipende da: {dependency_reason(d)}",
                      "need": "", "action": "riscrivi" if paragrafi else "verifica",
                      "depends": [d["id"]]})
    return fuori


def _is_dialogue(paragrafo: str) -> bool:
    """Un paragrafo con battute: virgolette o la lineetta del discorso diretto."""
    p = (paragrafo or "").lstrip()
    return bool(re.search(r"[«“\"]", p)) or p[:1] in ("—", "–") or p.startswith("- ")


# --------------------------------------------------------------------------- #
#  2. la mappa dell'impatto
# --------------------------------------------------------------------------- #
def chapter_paragraphs(text: str) -> list[str]:
    from bookwriter.forge.editor import parse

    return [p for s in parse(text).sections for p in s.paragraphs]


def scan_chapter(state: ForgeState, client: Any, number: int, title: str, text: str,
                 profilo: list[dict[str, Any]], *, model: str | None = None,
                 dependencies: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """I punti di un capitolo che un cambiamento tocca, direttamente o di riflesso.

    ``dependencies``: gli anelli della catena (:func:`dependencies`) che stanno in questo
    capitolo; la lettura li cerca, e un punto che nasce da uno di loro porta il suo id in
    ``depends`` e il suo «dipende da» nel ``what``."""
    blocchi = chapter_paragraphs(text)
    if not blocchi:
        return []
    anelli = {d["id"]: d for d in dependencies or []}
    numerati = "\n\n".join(f"[{i + 1}] {b}" for i, b in enumerate(blocchi))
    sistema = (
        "Leggi un capitolo di romanzo, paragrafo per paragrafo, sapendo che l'autore ha deciso i "
        "cambiamenti qui sotto. Trova OGNI punto che ne è toccato: dove il libro nomina il "
        "vecchio, e soprattutto dove ne subisce le conseguenze senza nominarlo — un viaggio, un "
        "biglietto, una strada, una moneta o una mancia, un cibo, un'usanza, una legge, il lato "
        "della strada, il clima, la stagione, il meteo, la luce, chi si incontra e come parla, "
        "l'atmosfera. Guarda sempre anche i personaggi (category "
        f"'{CHARACTERS}': chi è coinvolto, le relazioni, cosa sa ciascuno e da chi lo ha saputo) "
        f"e i dialoghi (category '{DIALOGUES}': le battute che nominano o presuppongono il "
        "vecchio, il modo di parlare che deve cambiare). Per ogni punto: 'paragraphs' (i "
        "numeri), 'change' (il numero del "
        "cambiamento), 'category', 'what' (cosa va cambiato, in una riga concreta), 'need' (la "
        "domanda da documentare per scriverlo giusto, o vuoto se non serve ricerca), 'action' "
        "('riscrivi', 'aggiungi' se serve un passaggio nuovo dopo quei paragrafi — es. il "
        "viaggio verso il luogo nuovo —, 'togli' se il paragrafo non ha più senso), 'depends' "
        "(gli id delle conseguenze a catena da cui il punto nasce, se ce ne sono; altrimenti "
        "vuoto). Se il capitolo non è toccato, 'impacts' è vuoto. Non inventare impatti per "
        f"riempire: un capitolo può restare com'è. Scrivi in {nb.language_name(state)}."
    )
    catena = ""
    if anelli:
        catena = ("# Le conseguenze a catena da cercare in questo capitolo\nVengono dal "
                  "registro del libro: il capitolo ne dipende. Trova dove il testo le mostra; se "
                  "il testo non ne è toccato davvero, non segnarle.\n" + "\n".join(
                      f"- [{d['id']}] {d['kind']}"
                      + (f" ({', '.join(d['who'])})" if d.get("who") else "")
                      + f": {d['fact']} — dipende da: {dependency_reason(d)}"
                      for d in anelli.values()) + "\n\n")
    utente = (f"# I cambiamenti e le differenze da considerare\n{_profile_text(profilo)}\n\n"
              + catena + f"# Capitolo {number}: {title}, a paragrafi numerati\n{numerati}")
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=SCAN_SCHEMA, model=model)
    except Exception as exc:  # noqa: BLE001
        return [{"paragraphs": [], "change": 0, "category": "errore",
                 "what": f"capitolo non letto: {str(exc)[:120]}", "need": "", "action": ""}]
    fuori = []
    for d in (r.get("impacts") or []) if isinstance(r, dict) else []:
        if not isinstance(d, dict):
            continue
        numeri = sorted({int(x) for x in d.get("paragraphs") or []
                         if isinstance(x, int) and 0 < x <= len(blocchi)})
        if not numeri or not str(d.get("what") or "").strip():
            continue
        azione = str(d.get("action") or "riscrivi").strip().lower()
        try:
            cambiamento = int(d.get("change") or 0)
        except (TypeError, ValueError):
            cambiamento = 0
        legati = [i for i in (str(x).strip().strip("[]") for x in d.get("depends") or [])
                  if i in anelli]
        cosa = str(d["what"]).strip()
        if legati:
            cosa += f" — dipende da: {dependency_reason(anelli[legati[0]])}"
        impatto = {"paragraphs": numeri, "change": cambiamento,
                   "category": category_key(str(d.get("category") or "")), "what": cosa,
                   "need": str(d.get("need") or "").strip(),
                   "action": azione if azione in ("riscrivi", "aggiungi", "togli")
                   else "riscrivi"}
        if legati:
            impatto["depends"] = list(dict.fromkeys(legati))
        fuori.append(impatto)
    return fuori


def term_hits(text: str, termini: list[str]) -> list[tuple[int, str]]:
    """Dove il testo nomina ancora il vecchio: (paragrafo, parola), per i termini del profilo."""
    blocchi = chapter_paragraphs(text)
    trovati = []
    for termine in termini:
        t = termine.strip()
        if len(t) < 3:
            continue
        schema = re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.IGNORECASE)
        for i, b in enumerate(blocchi):
            if schema.search(b):
                trovati.append((i + 1, t))
    return trovati


def plan(project: BookProject, state: ForgeState, client: Any, summary: str, *,
         model: str | None = None, progress=None, should_stop=None) -> dict[str, Any]:
    """Il piano delle modifiche: profilo, catena delle dipendenze, mappa dell'impatto, struttura
    con i capitoli da aggiornare e i loro punti di ricerca. Scrive ``story/impact.json``."""
    dati = load(project)
    dati["profile"] = profile(state, client, summary, model=model)
    dati["dependencies"], dati["dependency_errors"] = dependencies(
        project, state, client, dati["profile"], model=model, should_stop=should_stop)
    # un piano nuovo: la cronologia dopo le modifiche è ancora da ricostruire, e lo storico
    # delle applicazioni riparte
    dati["consistency"], dati["consistency_done"], dati["applied_history"] = {}, False, {}
    save(project, dati)
    termini = sorted({t for c in dati["profile"] for f in c.get("facets") or []
                      for t in f.get("old_terms") or []})
    scritti = project.written_chapters()
    dati["chapters"] = {}
    for i, c in enumerate(state.outline):
        if should_stop and should_stop():
            break
        if progress:
            progress(i, len(state.outline))
        n = int(c.get("number") or 0)
        if n not in scritti:
            continue
        testo = scritti[n].read_text(encoding="utf-8")
        blocchi = chapter_paragraphs(testo)
        anelli = [d for d in dati["dependencies"] if d["chapter"] == n]
        impatti = scan_chapter(state, client, n, str(c.get("title") or ""), testo,
                               dati["profile"], model=model, dependencies=anelli)
        # una parola del vecchio che la lettura non ha segnalato: la si aggiunge, da guardare
        segnalati = {p for imp in impatti for p in imp["paragraphs"]}
        for paragrafo, termine in term_hits(testo, termini):
            if paragrafo not in segnalati:
                impatto = {"paragraphs": [paragrafo], "change": 0,
                           "category": DIALOGUES if _is_dialogue(blocchi[paragrafo - 1])
                           else "rimando",
                           "what": f"il testo nomina ancora «{termine}»", "need": "",
                           "action": "riscrivi"}
                legati = [d for d in anelli if d.get("term", "").lower() == termine.lower()]
                if legati:  # la parola è già il perché: il legame resta per il grafo
                    impatto["depends"] = [d["id"] for d in legati]
                impatti.append(impatto)
                segnalati.add(paragrafo)
        impatti += _dependency_entries(blocchi, anelli, impatti)
        dati["chapters"][str(n)] = impatti
        # i punti «verifica» si vedono nel piano, ma da soli non fanno riscrivere il capitolo
        attivi = [imp for imp in impatti if imp.get("action") != "verifica"]
        domande = list(dict.fromkeys(imp["need"] for imp in attivi if imp.get("need")))
        c["status"] = TO_UPDATE if attivi else ORIGINAL
        c["points"] = domande[:6]
        if attivi and not c.get("does"):
            c["does"] = "; ".join(dict.fromkeys(imp["what"] for imp in attivi))[:300]
        save(project, dati)
    if progress:
        progress(len(state.outline), len(state.outline))
    return dati


def dependency_markdown(dati: dict[str, Any], *, heading: str = "###") -> list[str]:
    """La catena delle dipendenze in righe Markdown: per ogni cambiamento, l'albero degli anelli
    (rientrato per profondità), con capitolo, tipo, chi, il fatto e il perché."""
    anelli = dati.get("dependencies") or []
    errori = dati.get("dependency_errors") or []
    if not anelli and not errori:
        return []
    righe = [f"{heading} Le conseguenze a catena", ""]
    titoli = {int(c.get("n") or 0): str(c.get("change") or "") for c in dati.get("profile") or []}
    figli: dict[str, list[dict[str, Any]]] = {}
    for d in anelli:
        figli.setdefault(d.get("parent") or f"#{d.get('change')}", []).append(d)

    def ramo(chiave: str, livello: int) -> None:
        for d in sorted(figli.get(chiave, []), key=lambda x: (x["chapter"], x["id"])):
            chi = f" ({', '.join(d['who'])})" if d.get("who") else ""
            perche = f" — {d['because']}" if d.get("because") else ""
            fonte = "" if d.get("origin") == "modello" else " _[dal registro]_"
            righe.append(f"{'  ' * livello}- cap. {d['chapter']} · {d['kind']}{chi}: "
                         f"{d['fact']}{perche}{fonte}")
            ramo(d["id"], livello + 1)

    for n in sorted({int(d.get("change") or 0) for d in anelli} | set(titoli)):
        if not any(int(d.get("change") or 0) == n for d in anelli):
            continue
        righe.append(f"**Cambiamento {n}: {titoli.get(n, '')}**")
        ramo(f"#{n}", 0)
        righe.append("")
    for e in errori:
        righe.append(f"- _dipendenze non cercate (cambiamento {e.get('change')}): "
                     f"{e.get('error')}_")
    if errori:
        righe.append("")
    return righe


def _where(imp: dict[str, Any]) -> str:
    return ("§" + ",".join(map(str, imp["paragraphs"]))) if imp.get("paragraphs") else "§?"


def plan_markdown(dati: dict[str, Any], outline: list[dict[str, Any]]) -> str:
    """Il piano delle modifiche in forma leggibile, per la specifica."""
    righe = ["## Piano delle modifiche", ""]
    for c in dati.get("profile") or []:
        righe.append(f"### Cambiamento {c['n']}: {c['change']}")
        righe.append("")
        for f in c.get("facets") or []:
            interno = f.get("category") in NAMED_CATEGORIES
            righe.append(f"- **{f['category']}** — " + (f"oggi: {f['old']} · " if f.get("old")
                                                        else "")
                         + ("da controllare nel libro: " if interno else "da documentare: ")
                         + f"{f['question']}")
        righe.append("")
    righe += dependency_markdown(dati)
    righe += ["### Dove il libro ne è toccato", ""]
    per_categoria: dict[str, list[str]] = {CHARACTERS: [], DIALOGUES: []}
    for c in outline:
        impatti = (dati.get("chapters") or {}).get(str(c.get("number")), [])
        if not impatti:
            righe.append(f"- {c.get('number')}. {c.get('title')} — invariato")
            continue
        righe.append(f"- **{c.get('number')}. {c.get('title')}** — {len(impatti)} punti")
        for imp in impatti:
            azione = ("da guardare: la lettura non l'ha trovato nel testo"
                      if imp.get("action") == "verifica" else imp["action"])
            righe.append(f"  - {_where(imp)} [{imp['category']}] {imp['what']} ({azione})")
            if imp.get("category") in per_categoria:
                per_categoria[imp["category"]].append(
                    f"- cap. {c.get('number')} {_where(imp)}: {imp['what']}")
    righe.append("")
    for chiave in (CHARACTERS, DIALOGUES):
        righe += [f"### {chiave.capitalize()}", "",
                  *(per_categoria[chiave] or ["- nessun punto"]), ""]
    return "\n".join(righe).rstrip()


# --------------------------------------------------------------------------- #
#  3. l'applicazione
# --------------------------------------------------------------------------- #
def _groups(impatti: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gli impatti raggruppati per paragrafi che si toccano: un paragrafo si riscrive una volta
    sola, con tutte le ragioni insieme."""
    gruppi: list[dict[str, Any]] = []
    for imp in sorted(impatti, key=lambda x: min(x["paragraphs"])):
        insieme = set(imp["paragraphs"])
        for g in gruppi:
            if g["paragraphs"] & insieme or (
                    min(insieme) - max(g["paragraphs"]) == 1 and imp["action"] != "aggiungi"
                    and g["action"] != "aggiungi"):
                g["paragraphs"] |= insieme
                g["items"].append(imp)
                if imp["action"] == "aggiungi":
                    g["action"] = "aggiungi"
                break
        else:
            gruppi.append({"paragraphs": insieme, "items": [imp], "action": imp["action"]})
    return gruppi


def _applied_items(punti: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """I punti di un gruppo applicato, come li ricorda il registro: categoria, motivo, paragrafi
    del punto (il gruppo può unirne di vicini), anelli."""
    return [{"category": category_key(str(i.get("category") or "")), "what": i.get("what", ""),
             "paragraphs": list(i.get("paragraphs") or []),
             "depends": list(i.get("depends") or [])} for i in punti]


def apply_chapter(project: BookProject, state: ForgeState, client: Any, chapter: dict[str, Any],
                  pack: ContextPack, *, model: str | None = None, style_contract: str = "",
                  should_stop=None, on_section=None) -> dict[str, Any]:
    """Applica la mappa dell'impatto a un capitolo: si riscrivono solo i paragrafi toccati."""
    from bookwriter.forge.editor import parse
    from bookwriter.forge.writing import _NO_INVENTION, FORBIDDEN

    dati = load(project)
    numero = int(chapter.get("number") or 0)
    titolo = str(chapter.get("title") or "")
    percorso = project.chapter_path(numero, titolo)
    capitolo = parse(percorso.read_text(encoding="utf-8"))
    posti = [(si, pi) for si, s in enumerate(capitolo.sections) for pi in range(len(s.paragraphs))]
    blocchi = [capitolo.sections[si].paragraphs[pi] for si, pi in posti]
    impatti = (dati.get("chapters") or {}).get(str(numero), [])
    profilo = _profile_text(dati.get("profile") or [])
    sistema = (
        "Sei il romanziere di questo libro e lo stai aggiornando: l'autore ha deciso i "
        "cambiamenti qui sotto. Riscrivi SOLO i paragrafi che ti do, applicando i cambiamenti e "
        "tutte le loro conseguenze (luoghi, viaggio, persone, lingua, moneta e mance, cibo, "
        "usanze, leggi, guida, clima e stagione, meteo, atmosfera) con i dati del materiale di "
        "ricerca — un dettaglio culturale giusto vale più di una descrizione lunga. Tutto il "
        "resto resta com'è: la trama, i personaggi, le loro parole, quello che succede — "
        f"salvo dove un punto '{CHARACTERS}' chiede di cambiare chi sa cosa o una relazione, "
        f"un punto '{DIALOGUES}' una battuta o il modo di parlare, un punto che «dipende da» "
        "un fatto cambiato le sue conseguenze: lì cambi quello, e solo quello. "
        "Stessa voce, stesso ritmo, stessa lunghezza circa (il brano originale qui sotto è il "
        "modello). 'rewritten' ha un elemento per ogni paragrafo dato, nell'ordine (vuoto solo "
        "se il paragrafo va tolto); 'insert_after' è un passaggio nuovo da mettere dopo l'ultimo "
        "paragrafo, solo se richiesto, altrimenti vuoto.\n\n"
        f"Lingua: {pack.language}. Non scrivere mai nel testo: {FORBIDDEN}.\n\n" + _NO_INVENTION
        + (f"\n\nStile richiesto:\n{style_contract}" if style_contract else "")
    )
    gruppi = _groups([i for i in impatti
                      if i.get("paragraphs") and i.get("action") != "verifica"])
    nuovi: dict[int, str | None] = {}
    aggiunte: dict[int, str] = {}
    registro = []
    for k, g in enumerate(gruppi):
        if should_stop and should_stop():
            break
        if on_section:
            on_section(k + 1, len(gruppi))
        numeri = sorted(g["paragraphs"])
        prima = blocchi[numeri[0] - 2] if numeri[0] > 1 else ""
        dopo = blocchi[numeri[-1]] if numeri[-1] < len(blocchi) else ""
        utente = "\n\n".join(p for p in (
            f"# I cambiamenti\n{profilo}", pack.voice_block(), pack.claims_block(),
            pack.decisions_block(),
            "# Cosa cambiare in questi paragrafi\n" + "\n".join(
                f"- [{i['category']}] {i['what']}" + (" — serve un passaggio nuovo dopo"
                                                     if i["action"] == "aggiungi" else "")
                + (" — il paragrafo va tolto" if i["action"] == "togli" else "")
                for i in g["items"]),
            f"# Il paragrafo prima (resta com'è)\n{prima}" if prima else "",
            "# I paragrafi da riscrivere\n" + "\n\n".join(f"[{n}] {blocchi[n - 1]}"
                                                          for n in numeri),
            f"# Il paragrafo dopo (resta com'è)\n{dopo}" if dopo else "") if p)
        try:
            r = client.chat_json([{"role": "system", "content": sistema},
                                  {"role": "user", "content": utente}],
                                 schema=APPLY_SCHEMA, model=model)
        except Exception as exc:  # noqa: BLE001 - quel gruppo resta com'era, e lo si dice
            registro.append({"paragraphs": numeri, "error": str(exc)[:160],
                             "items": _applied_items(g["items"])})
            continue
        riscritti = [str(x).strip() for x in (r.get("rewritten") or [])] \
            if isinstance(r, dict) else []
        if len(riscritti) != len(numeri):
            registro.append({"paragraphs": numeri, "error": "il modello non ha restituito un "
                             "paragrafo per ciascuno: lasciati com'erano",
                             "items": _applied_items(g["items"])})
            continue
        togli = any(i["action"] == "togli" for i in g["items"])
        prima_parole = sum(count_words(blocchi[n - 1]) for n in numeri)
        dopo_parole = sum(count_words(x) for x in riscritti)
        if not togli and prima_parole and dopo_parole < 0.5 * prima_parole:
            registro.append({"paragraphs": numeri, "error": f"riscrittura troppo corta "
                             f"({dopo_parole} parole su {prima_parole}): lasciati com'erano",
                             "items": _applied_items(g["items"])})
            continue
        for n, nuovo in zip(numeri, riscritti, strict=True):
            if nuovo != blocchi[n - 1]:
                nuovi[n] = nuovo or None
        inserto = str((r or {}).get("insert_after") or "").strip()
        if inserto and g["action"] == "aggiungi":
            aggiunte[numeri[-1]] = inserto
        registro.append({"paragraphs": numeri, "reasons": [i["what"] for i in g["items"]],
                         "added": bool(inserto and g["action"] == "aggiungi"),
                         "items": _applied_items(g["items"])})
    # il capitolo ricomposto
    for indice, (si, pi) in enumerate(posti, 1):
        if indice in nuovi:
            capitolo.sections[si].paragraphs[pi] = nuovi[indice] or ""
    for indice in sorted(aggiunte, reverse=True):
        si, pi = posti[indice - 1]
        capitolo.sections[si].paragraphs.insert(pi + 1, aggiunte[indice])
    for sezione in capitolo.sections:
        sezione.paragraphs = [p for p in sezione.paragraphs if p]
    percorso.write_text(capitolo.render(), encoding="utf-8")
    dati.setdefault("applied", {})[str(numero)] = registro
    save(project, dati)
    chapter["status"] = UPDATED
    return {"path": percorso, "changed": len(nuovi), "added": len(aggiunte),
            "errors": [x for x in registro if x.get("error")]}


# --------------------------------------------------------------------------- #
#  4. i residui e il documento delle modifiche
# --------------------------------------------------------------------------- #
def residual_sweep(project: BookProject, state: ForgeState) -> list[dict[str, Any]]:
    """Le parole del vecchio rimaste nel libro aggiornato. Diventano impatti da riguardare: il
    capitolo torna «da aggiornare» una volta sola (``residual_done``)."""
    dati = load(project)
    termini = sorted({t for c in dati.get("profile") or [] for f in c.get("facets") or []
                      for t in f.get("old_terms") or []})
    scritti = project.written_chapters()
    residui = []
    for c in state.outline:
        n = int(c.get("number") or 0)
        if n not in scritti:
            continue
        for paragrafo, termine in term_hits(scritti[n].read_text(encoding="utf-8"), termini):
            residui.append({"chapter": n, "paragraph": paragrafo, "term": termine})
    dati["residual"] = residui
    save(project, dati)
    return residui


def requeue_residuals(project: BookProject, state: ForgeState,
                      residui: list[dict[str, Any]]) -> int:
    """I residui tornano da riguardare, una volta: se dopo la seconda passata restano, è
    probabile che siano voluti (un ricordo del luogo vecchio, un personaggio che ne parla)."""
    dati = load(project)
    if dati.get("residual_done"):
        return 0
    per_capitolo: dict[int, list[dict[str, Any]]] = {}
    for r in residui:
        per_capitolo.setdefault(r["chapter"], []).append(r)
    for c in state.outline:
        n = int(c.get("number") or 0)
        if n in per_capitolo:
            _archive_applied(dati, n)
            dati["chapters"][str(n)] = [{"paragraphs": [r["paragraph"]], "change": 0,
                                         "category": "residuo",
                                         "what": f"il testo nomina ancora «{r['term']}»: "
                                                 "aggiornalo, a meno che il rimando al vecchio "
                                                 "sia voluto dal racconto",
                                         "need": "", "action": "riscrivi"}
                                        for r in per_capitolo[n]]
            c["status"] = TO_UPDATE
    dati["residual_done"] = True
    save(project, dati)
    return len(per_capitolo)


def _archive_applied(dati: dict[str, Any], numero: int) -> None:
    """Quello che il giro prima ha applicato a un capitolo passa nello storico: il giro nuovo
    (residui, cronologia) non lo cancella dal documento delle modifiche."""
    fatto = (dati.get("applied") or {}).pop(str(numero), None)
    if fatto:
        dati.setdefault("applied_history", {}).setdefault(str(numero), []).append(fatto)


# --------------------------------------------------------------------------- #
#  5. la cronologia ricostruita dopo le modifiche
# --------------------------------------------------------------------------- #
def consistency_check(project: BookProject, state: ForgeState, client: Any, *,
                      model: str | None = None, progress=None, should_stop=None
                      ) -> dict[str, Any]:
    """Ricostruisce, sul testo aggiornato, la cronologia dei capitoli toccati (quelli applicati
    e quelli della catena delle dipendenze) e ne controlla la coerenza: orari in ordine,
    spostamenti mostrati (anche dal capitolo prima), scadenze con un'ora sola, oggetti sottratti
    una volta, date con la stagione; e, fra capitoli, scadenze e oggetti a cavallo.

    Ogni scena è una chiamata breve (:func:`bookwriter.forge.timeline.record_scene`); una che
    non riesce si annota e il controllo prosegue. Le cronologie si salvano accanto alle altre
    (``story/timeline/``); il resoconto va in ``impact.json`` sotto ``consistency``:
    ``chapters`` (i capitoli ricostruiti), ``issues`` (capitolo, gravità, tipo, problema,
    citazione, correzione, sezione, origine), ``errors``.
    """
    from dataclasses import asdict

    from bookwriter.forge import ledger as lg
    from bookwriter.forge import timeline as tl
    from bookwriter.forge.editor import parse

    dati = load(project)
    scritti = project.written_chapters()
    titoli = {int(c.get("number") or 0): str(c.get("title") or "") for c in state.outline}
    toccati = {int(n) for n in dati.get("applied") or {}} | {
        int(n) for n in dati.get("applied_history") or {}} | {
        int(d.get("chapter") or 0) for d in dati.get("dependencies") or []}
    da_vedere = sorted(n for n in toccati if n in scritti)
    registro = lg.load(project)
    esito: dict[str, Any] = {"chapters": [], "issues": [], "errors": []}
    cronologie: dict[int, list[Any]] = {}
    for k, n in enumerate(da_vedere):
        if should_stop and should_stop():
            break
        if progress:
            progress(k, len(da_vedere))
        sezioni = [(s.heading, s.text) for s in
                   parse(scritti[n].read_text(encoding="utf-8")).sections]
        precedente = registro.previous(n)
        valido = precedente is not None and not precedente.error
        inizio = list(precedente.end_state.lines()) if valido else []
        luogo_prima = precedente.end_state.place if valido else ""
        if n - 1 in cronologie:  # il capitolo prima è appena stato ricostruito: vale quello
            presente = [r for r in cronologie[n - 1] if not r.error and not r.flashback]
            if presente:
                luogo_prima = presente[-1].place_end or presente[-1].place_start or luogo_prima
        records: list[Any] = []
        problemi: list[Any] = []
        for i, (titolo, corpo) in enumerate(sezioni):
            if not corpo.strip():
                records.append(tl.SceneRecord(heading=titolo, error="vuota"))
                continue
            record, trovati = tl.record_scene(client, titolo, corpo, records, inizio,
                                              model=model)
            records.append(record)
            if record.error:
                esito["errors"].append({"chapter": n, "section": i + 1, "error": record.error})
            for p in trovati:
                p.section = i
            problemi += trovati
        problemi = tl._dedup(problemi + tl.check(records, luogo_prima))
        cronologie[n] = records
        try:
            tl.save(tl.timeline_path(project, n, titoli.get(n, "")),
                    tl.Timeline(records=records, issues=problemi))
        except OSError as exc:
            esito["errors"].append({"chapter": n, "section": 0, "error": str(exc)[:160]})
        esito["chapters"].append(n)
        esito["issues"] += [{"chapter": n, **asdict(p)} for p in problemi]
    for n, p in tl.check_book(list(cronologie.items())):
        esito["issues"].append({"chapter": n, **asdict(p)})
    if progress:
        progress(len(da_vedere), len(da_vedere))
    dati = load(project)
    dati["consistency"] = esito
    save(project, dati)
    return esito


def _requeue_worthy(problema: dict[str, Any]) -> bool:
    """Un problema di cronologia da riguardare: i bloccanti sempre, gli importanti se li ha
    trovati un controllo deterministico (quelli del modello si leggono, non si inseguono)."""
    gravita = problema.get("severity")
    if gravita == "bloccante":
        return True
    return gravita == "importante" and problema.get("origin") == "regola"


def _issue_paragraph(sezioni: list[list[str]], problema: dict[str, Any]) -> int:
    """Il paragrafo (contato su tutto il capitolo) a cui un problema di cronologia si riferisce:
    quello che contiene la citazione, se c'è, altrimenti il primo della sua sezione."""
    inizio, primo_di = 0, []
    for paragrafi in sezioni:
        primo_di.append(inizio)
        inizio += len(paragrafi)
    if not inizio:
        return 0
    try:
        sezione = int(problema.get("section", -1))
    except (TypeError, ValueError):
        sezione = -1
    citazione = str(problema.get("quote") or "").strip().lower()[:60]
    if citazione:
        candidati = [sezione] if 0 <= sezione < len(sezioni) else range(len(sezioni))
        for s in candidati:
            for j, p in enumerate(sezioni[s]):
                if citazione in p.lower():
                    return primo_di[s] + j + 1
    if 0 <= sezione < len(sezioni) and sezioni[sezione]:
        return primo_di[sezione] + 1
    return 1


def requeue_consistency(project: BookProject, state: ForgeState) -> int:
    """I problemi di cronologia trovati dopo le modifiche tornano da riguardare, una volta:
    diventano impatti «cronologia» del loro capitolo, con la correzione minima. Ritorna quanti
    capitoli tornano «da aggiornare»."""
    from bookwriter.forge.editor import parse

    dati = load(project)
    if dati.get("consistency_done"):
        return 0
    per_capitolo: dict[int, list[dict[str, Any]]] = {}
    for p in (dati.get("consistency") or {}).get("issues") or []:
        if _requeue_worthy(p):
            per_capitolo.setdefault(int(p.get("chapter") or 0), []).append(p)
    scritti = project.written_chapters()
    rimessi = 0
    for c in state.outline:
        n = int(c.get("number") or 0)
        if n not in per_capitolo or n not in scritti:
            continue
        sezioni = [s.paragraphs for s in parse(scritti[n].read_text(encoding="utf-8")).sections]
        impatti = []
        for p in per_capitolo[n]:
            paragrafo = _issue_paragraph(sezioni, p)
            if not paragrafo:
                continue
            impatti.append({"paragraphs": [paragrafo], "change": 0, "category": CHRONOLOGY,
                            "what": f"cronologia dopo le modifiche ({p.get('kind') or 'eventi'}): "
                                    f"{p.get('problem')}"
                                    + (f" — correzione minima: {p['fix']}" if p.get("fix")
                                       else ""),
                            "need": "", "action": "riscrivi"})
        if impatti:
            _archive_applied(dati, n)
            dati["chapters"][str(n)] = impatti
            c["status"] = TO_UPDATE
            rimessi += 1
    dati["consistency_done"] = True
    save(project, dati)
    return rimessi


def pending(state: ForgeState) -> list[dict[str, Any]]:
    return [c for c in state.outline if c.get("status") == TO_UPDATE]


def applied_rounds(dati: dict[str, Any], numero: int) -> list[list[dict[str, Any]]]:
    """Le applicazioni di un capitolo, giro per giro: la prima, poi residui e cronologia."""
    giri = list((dati.get("applied_history") or {}).get(str(numero), []))
    ultimo = (dati.get("applied") or {}).get(str(numero))
    if ultimo:
        giri.append(ultimo)
    return [g for g in giri if g]


def by_category(dati: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """I punti applicati raggruppati per categoria, con ``personaggi`` e ``dialoghi`` sempre per
    primi (anche vuoti): categoria → [{chapter, paragraphs, what, error, round}]."""
    gruppi: dict[str, list[dict[str, Any]]] = {CHARACTERS: [], DIALOGUES: []}
    numeri = sorted({int(n) for n in dati.get("applied") or {}}
                    | {int(n) for n in dati.get("applied_history") or {}})
    for n in numeri:
        for giro, voci in enumerate(applied_rounds(dati, n), 1):
            for x in voci:
                punti = x.get("items") or [{"category": "", "what": r}
                                           for r in x.get("reasons") or []]
                for punto in punti:
                    chiave = category_key(str(punto.get("category") or "")) or "altro"
                    gruppi.setdefault(chiave, []).append({
                        "chapter": n,
                        "paragraphs": list(punto.get("paragraphs") or x.get("paragraphs") or []),
                        "what": str(punto.get("what") or ""), "error": x.get("error") or "",
                        "round": giro})
    return gruppi


def revision_report(project: BookProject, state: ForgeState):
    """Il libro aggiornato confrontato con l'originale, paragrafo per paragrafo, come
    ``RevisionReport`` (lo stesso formato con cui «Integra dagli appunti» segna le modifiche)."""
    from bookwriter.forge.manuscript import originals
    from bookwriter.update.revise import Change, RevisionReport

    dati = load(project)
    orig = originals(project)
    scritti = project.written_chapters()
    modifiche: list[Change] = []
    for c in state.outline:
        n = int(c.get("number") or 0)
        titolo = str(c.get("title") or "")
        fonte = int(c.get("source") or n)
        prima = chapter_paragraphs(orig[fonte].read_text(encoding="utf-8")) if fonte in orig else []
        dopo = chapter_paragraphs(scritti[n].read_text(encoding="utf-8")) if n in scritti else []
        # il primo giro è numerato sull'originale: le sue ragioni valgono per prime
        ragioni: dict[int, str] = {}
        for giro in applied_rounds(dati, n):
            for x in giro:
                for p in x.get("paragraphs") or []:
                    ragioni.setdefault(p, "; ".join(x.get("reasons") or []))
        confronto = difflib.SequenceMatcher(a=prima, b=dopo, autojunk=False)
        for op, a1, a2, b1, b2 in confronto.get_opcodes():
            if op == "equal":
                modifiche += [Change("keep", titolo, t) for t in dopo[b1:b2]]
            elif op == "replace":
                coppie = max(a2 - a1, b2 - b1)
                for k in range(coppie):
                    vecchio = prima[a1 + k] if a1 + k < a2 else ""
                    nuovo = dopo[b1 + k] if b1 + k < b2 else ""
                    motivo = ragioni.get(a1 + k + 1, "")
                    if vecchio and nuovo:
                        modifiche.append(Change("replace", titolo, nuovo, previous=vecchio,
                                                reason=motivo))
                    elif nuovo:
                        modifiche.append(Change("add", titolo, nuovo, reason=motivo))
                    else:
                        modifiche.append(Change("remove", titolo, vecchio, reason=motivo))
            elif op == "insert":
                modifiche += [Change("add", titolo, t, reason="passaggio nuovo")
                              for t in dopo[b1:b2]]
            elif op == "delete":
                modifiche += [Change("remove", titolo, t) for t in prima[a1:a2]]
    return RevisionReport(modifiche)


def consistency_markdown(dati: dict[str, Any], *, heading: str = "##") -> list[str]:
    """La cronologia ricostruita dopo le modifiche, in righe Markdown."""
    esito = dati.get("consistency") or {}
    if not esito:
        return []
    from bookwriter.forge.timeline import SEVERITIES

    righe = [f"{heading} La cronologia dopo le modifiche", ""]
    capitoli = esito.get("chapters") or []
    righe.append("Capitoli ricostruiti: " + (", ".join(map(str, capitoli)) or "nessuno") + ".")
    righe.append("")
    problemi = sorted(esito.get("issues") or [], key=lambda p: (
        SEVERITIES.index(p.get("severity")) if p.get("severity") in SEVERITIES else 9,
        int(p.get("chapter") or 0)))
    if not problemi:
        righe.append("- orari, spostamenti e scadenze tornano")
    for p in problemi:
        sezione = int(p.get("section", -1)) if str(p.get("section", "")).lstrip("-").isdigit() \
            else -1
        dove = f"cap. {p.get('chapter')}" + (f", scena {sezione + 1}" if sezione >= 0 else "")
        righe.append(f"- [{p.get('severity')}] {dove} — {p.get('problem')}"
                     + (f" («{str(p['quote'])[:120]}»)" if p.get("quote") else "")
                     + (f" → {p['fix']}" if p.get("fix") else "")
                     + ("" if p.get("origin") == "regola" else " _[dal modello]_"))
    for e in esito.get("errors") or []:
        righe.append(f"- _cap. {e.get('chapter')}, scena {e.get('section')}: non letta — "
                     f"{e.get('error')}_")
    righe.append("")
    return righe


def write_outputs(project: BookProject, state: ForgeState, fmt: str = "docx") -> list[Path]:
    """Il documento con le modifiche segnate (``fmt`` + HTML) e il resoconto in Markdown: capitolo
    per capitolo, per categoria (personaggi e dialoghi per primi), la catena delle dipendenze,
    la cronologia dopo le modifiche e i rimandi al vecchio rimasti."""
    from bookwriter.update.render import write_revision, write_revision_html

    rapporto = revision_report(project, state)
    cartella = project.out_dir
    cartella.mkdir(parents=True, exist_ok=True)
    titolo = (nb.get(state, "title") or "Libro") + " — aggiornamento"
    fatti = [write_revision(rapporto, cartella / f"modifiche_segnate.{fmt}", fmt, title=titolo),
             write_revision_html(rapporto, cartella / "modifiche_segnate.html", title=titolo)]
    dati = load(project)
    righe = [f"# {titolo}", "", f"Modifiche: {rapporto.summary()}", ""]
    for c in dati.get("profile") or []:
        righe += [f"## Cambiamento {c['n']}: {c['change']}", ""]
    righe += ["## Capitolo per capitolo", ""]
    for c in state.outline:
        giri = applied_rounds(dati, int(c.get("number") or 0))
        if not giri:
            continue
        righe.append(f"### {c.get('number')}. {c.get('title')}")
        for k, applicate in enumerate(giri):
            giro = "" if k == 0 else f" _(giro {k + 1})_"
            for x in applicate:
                dove = "§" + ",".join(map(str, x.get("paragraphs") or []))
                if x.get("error"):
                    righe.append(f"- {dove}: NON applicato — {x['error']}{giro}")
                else:
                    righe.append(f"- {dove}: " + "; ".join(x.get("reasons") or [])
                                 + (" (+ passaggio nuovo)" if x.get("added") else "") + giro)
        righe.append("")
    righe += ["## Per categoria", ""]
    for chiave, voci in by_category(dati).items():
        if not voci and chiave not in NAMED_CATEGORIES:
            continue
        righe += [f"### {chiave.capitalize()}", ""]
        if not voci:
            righe.append("- nessuna modifica")
        for v in voci:
            dove = f"cap. {v['chapter']} §" + ",".join(map(str, v["paragraphs"]))
            righe.append(f"- {dove}: {v['what']}"
                         + (f" — NON applicato: {v['error']}" if v["error"] else ""))
        righe.append("")
    righe += dependency_markdown(dati, heading="##")
    da_guardare = [(n, imp) for n, impatti in sorted((dati.get("chapters") or {}).items(),
                                                     key=lambda x: int(x[0]))
                   for imp in impatti if imp.get("action") == "verifica"]
    if da_guardare:
        righe += ["## Conseguenze da guardare a mano", "",
                  "La catena le segnala, ma la lettura non le ha trovate nel testo.", "",
                  *[f"- cap. {n} [{imp.get('category')}]: {imp.get('what')}"
                    for n, imp in da_guardare], ""]
    righe += consistency_markdown(dati)
    if dati.get("residual"):
        righe += ["## Rimandi al vecchio rimasti", "",
                  *[f"- capitolo {r['chapter']} §{r['paragraph']}: «{r['term']}»"
                    for r in dati["residual"]], ""]
    resoconto = cartella / "modifiche.md"
    resoconto.write_text("\n".join(righe), encoding="utf-8")
    fatti.append(resoconto)
    return fatti
