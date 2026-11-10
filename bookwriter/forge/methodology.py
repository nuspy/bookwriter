"""La metodologia di un testo: l'LLM decide che cosa controllare, il codice controlla i conti.

Dalla specifica metodologica del 25/10/2026 (``docs/SPECIFICA_METODOLOGIA_LLM_CLAUDE_CODE_THE_RED_THREAD.md``).
Nella prova v4 del cap. 11 i difetti più gravi erano conti che non tornavano: i nodi distribuiti
9 + 5 + 2 = 16 su un totale di 14, le schede dell'indice 11, 14 e 8 nello stesso pomeriggio. Nessuno
schema fisso li prevede: sono entità che **quel** testo rende importanti. Qui:

- **il profilo dell'opera** (:func:`work_profile`, livello A): tipo, genere, pubblico, scopo, lingua,
  misura — dal quaderno, senza modello;
- **il profilo del progetto** (:func:`project_profile`, livello B): le regole esplicite
  dell'autore (requisiti, esclusioni, vincoli, canone), in ``story/profilo_progetto.json``,
  versionato; si ricava una volta e lo si corregge, non si reinventa a ogni analisi;
- **la metodologia** (:func:`generate`, livello E): per un testo (un piano o un capitolo) e uno
  scopo, l'LLM propone criteri con pesi e motivazione, **registri** (le entità da tracciare, con
  categorie, totale canonico, stato iniziale) e i loro **invarianti** testabili, i motivi da
  sottoporre ad audit, i rischi, i test, le condizioni di blocco, le domande all'autore. Il codice
  la **valida** (:func:`validate_methodology`: invarianti di tipo noto e con i parametri che
  servono, tetti al numero) e la salva versionata in ``story/metodologia/``. È una proposta
  subordinata al canone: i conflitti si segnalano, non si appianano;
- **l'estrazione** (:func:`extract`, livello F): per ogni sezione l'LLM riporta le osservazioni
  dei registri — una distribuzione con le parti, un conteggio presente/rimosso/aggiunto, il luogo
  di un'entità — con la citazione come prova, e ``unknown``/``ambiguous`` quando non sa;
- **il validatore** (:func:`check`, livello G): somme rispetto al totale (solo se le categorie sono
  disgiunte ed esaustive: altrimenti ``not_run``), conservazione dei conteggi nel tempo, un'entità in
  un solo luogo alla volta. Esito ``pass``, ``fail``, ``ambiguous`` o ``not_run``, con l'evidenza.

**L'LLM interpreta e propone. Il canone vincola. Il codice verifica.**
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: I tipi di invariante che il codice sa verificare.
INVARIANT_KINDS = ("sum_to_total", "conservation", "single_place")
#: I tetti: la metodologia deve restare utile, non diventare un inventario (specifica §10.1).
MAX_REGISTERS = 6
MAX_MOTIFS = 5
MAX_TESTS = 10

_T = {"type": "string"}
_L = {"type": "array", "items": {"type": "string"}}


# --------------------------------------------------------------------------- #
#  A. il profilo dell'opera
# --------------------------------------------------------------------------- #
def work_profile(state: Any) -> dict[str, Any]:
    """Tipo, genere, pubblico, scopo, lingua, misura e struttura: dal quaderno e dalla struttura."""
    from bookwriter.forge import notebook as nb
    from bookwriter.forge import quality as ql

    voci = nb.normalise(getattr(state, "notebook", {}) or {})

    def v(k: str) -> str:
        x = voci.get(k)
        return "; ".join(str(y) for y in x if y) if isinstance(x, list) else str(x or "").strip()

    struttura = getattr(state, "outline", []) or []
    tipo = str((getattr(state, "quality", {}) or {}).get("kind") or "") or ql.kind_of(
        v("form"), nb.is_narrative(state))
    return {
        "type": tipo, "type_label": ql.KINDS.get(tipo, tipo), "genre": v("form"),
        "audience": v("audience"), "purpose": v("author_goal"), "promise": v("reader_effect"),
        "language": nb.language_name(state), "chapters": len(struttura),
        "words": sum(int(c.get("words") or 0) for c in struttura),
        "requirements": v("agreed"), "exclusions": v("avoid"), "style_notes": v("style_notes"),
    }


def profile_text(profilo: dict[str, Any]) -> str:
    righe = [f"- tipo di opera: {profilo.get('type_label') or profilo.get('type')}"]
    for nome, k in (("genere e forma", "genre"), ("pubblico", "audience"), ("scopo", "purpose"),
                    ("promessa al lettore", "promise"), ("lingua", "language"),
                    ("requisiti", "requirements"), ("esclusioni", "exclusions"),
                    ("note di stile", "style_notes")):
        if profilo.get(k):
            righe.append(f"- {nome}: {str(profilo[k])[:600]}")
    if profilo.get("chapters"):
        righe.append(f"- struttura: {profilo['chapters']} capitoli, circa {profilo['words']} "
                     "parole")
    return "\n".join(righe)


# --------------------------------------------------------------------------- #
#  B. il profilo del progetto: le regole esplicite dell'autore
# --------------------------------------------------------------------------- #
_PROJECT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"rules": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"text": _T,
                       "kind": {"type": "string",
                                "enum": ["requisito", "esclusione", "vincolo", "canone"]},
                       "source": _T},
        "required": ["text", "kind", "source"]}}},
    "required": ["rules"],
}


def project_profile_path(project) -> Path:
    return project.story_dir / "profilo_progetto.json"


def project_profile(project, state: Any, client: Any = None, *, model: str | None = None,
                    refresh: bool = False) -> dict[str, Any]:
    """Il profilo del progetto: dal file se c'è; altrimenti (o con ``refresh``) l'LLM elenca le
    regole esplicite dell'autore dal quaderno, dalla specifica e dalle decisioni, e si salva."""
    path = project_profile_path(project)
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    from bookwriter.forge import notebook as nb

    profilo = {"version": 0, "updated": "", "rules": [], "reason": "nessun modello"}
    if path.exists():
        try:
            profilo = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    if client is None:
        return profilo
    spec = project.spec_path.read_text(encoding="utf-8")[:20000] \
        if project.spec_path.exists() else ""
    sistema = (
        "Elenca le REGOLE ESPLICITE dell'autore per questo libro: requisiti (cosa deve esserci), "
        "esclusioni (cosa non deve esserci), vincoli (lingua, misura, struttura, punto di vista, "
        "cosa il narratore non può rivelare e fino a quando), canone (fatti del mondo del libro "
        "che non si cambiano). Solo quello che l'autore ha detto o deciso, con la fonte "
        "('source': quaderno, specifica, decisione); niente regole inventate o generiche. Una "
        "regola per voce, breve. Al più 30."
    )
    utente = "\n\n".join(p for p in (
        f"# Il quaderno\n{nb.brief(state)}", f"# La specifica\n{spec}" if spec else "") if p)
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=_PROJECT_SCHEMA, model=model)
    except Exception:  # noqa: BLE001 - senza profilo resta quello di prima
        return profilo
    regole = [{"id": f"R{i:02d}", "text": str(x.get("text") or "").strip(),
               "kind": str(x.get("kind") or "vincolo"), "source": str(x.get("source") or "")}
              for i, x in enumerate((r.get("rules") or [])[:30], 1)
              if isinstance(x, dict) and str(x.get("text") or "").strip()]
    profilo = {"version": int(profilo.get("version") or 0) + 1,
               "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "rules": regole, "reason": "ricavato dal quaderno e dalla specifica"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profilo, ensure_ascii=False, indent=1), encoding="utf-8")
    return profilo


def rules_text(profilo: dict[str, Any]) -> str:
    return "\n".join(f"- [{r.get('kind')}] {r.get('text')}" for r in profilo.get("rules") or [])


# --------------------------------------------------------------------------- #
#  E. la metodologia
# --------------------------------------------------------------------------- #
@dataclass
class Register:
    id: str
    name: str = ""
    purpose: str = ""  #: il rischio che previene
    entity: str = ""   #: che cos'è un elemento (una scheda d'archivio, un nodo del pattern…)
    unit: str = ""
    categories: list[str] = field(default_factory=list)
    disjoint: bool = False   #: le categorie non si sovrappongono
    exhaustive: bool = False  #: le categorie coprono il totale
    total: int | None = None  #: il totale canonico, se il canone lo fissa
    initial: str = ""
    invariants: list[str] = field(default_factory=list)  #: tipi da INVARIANT_KINDS
    certainty: str = ""
    on_missing: str = "ambiguous"


@dataclass
class Methodology:
    scope: str = "testo"  #: «piano» o «testo»
    chapter: int = 0
    version: int = 1
    created: str = ""
    reason: str = ""
    criteria: list[dict[str, Any]] = field(default_factory=list)
    registers: list[Register] = field(default_factory=list)
    motifs: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    tests: list[dict[str, str]] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    author_questions: list[str] = field(default_factory=list)
    #: cosa la validazione ha tolto o corretto, e perché
    validation: list[str] = field(default_factory=list)

    def register(self, ident: str) -> Register | None:
        return next((r for r in self.registers if r.id == ident), None)

    def canon_block(self) -> str:
        """I registri come vincoli per chi scrive: totali, categorie, stato iniziale."""
        righe = []
        for r in self.registers:
            pezzi = [f"{r.name or r.id}: {r.entity}"]
            if r.total is not None:
                pezzi.append(f"totale canonico {r.total}")
            if r.categories:
                pezzi.append("categorie " + ", ".join(r.categories)
                             + (" (disgiunte)" if r.disjoint else ""))
            if r.initial:
                pezzi.append(f"all'inizio: {r.initial}")
            righe.append("- " + "; ".join(pezzi))
        if not righe:
            return ""
        return ("# I registri del capitolo: numeri e stati che il testo deve rispettare (ogni "
                "cambiamento si mostra in scena; schede, riferimenti e nodi sono cose diverse)\n"
                + "\n".join(righe))


_METHOD_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "criteria": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"key": _T, "why": _T, "weight": {"type": "number"}},
            "required": ["key", "why", "weight"]}},
        "registers": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": _T, "name": _T, "purpose": _T, "entity": _T, "unit": _T,
                           "categories": _L, "disjoint": {"type": "boolean"},
                           "exhaustive": {"type": "boolean"},
                           "total": {"type": ["integer", "null"]}, "initial": _T,
                           "invariants": {"type": "array", "items": {
                               "type": "string", "enum": list(INVARIANT_KINDS)}},
                           "certainty": _T},
            "required": ["id", "name", "purpose", "entity", "unit", "categories", "disjoint",
                         "exhaustive", "total", "initial", "invariants", "certainty"]}},
        "motifs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": _T, "name": _T, "aliases": _L, "meaning": _T,
                           "functions": _L},
            "required": ["id", "name", "aliases", "meaning", "functions"]}},
        "sources": _L, "risks": _L,
        "tests": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": _T, "description": _T, "register": _T},
            "required": ["id", "description", "register"]}},
        "blocking": _L, "author_questions": _L,
    },
    "required": ["criteria", "registers", "motifs", "sources", "risks", "tests", "blocking",
                 "author_questions"],
}


def generate(client: Any, text: str, *, scope: str, chapter: int, work: dict[str, Any],
             project: dict[str, Any], objective: str, canon: str = "",
             model: str | None = None) -> Methodology:
    """La metodologia proposta dall'LLM per questo testo, già validata (vedi
    :func:`validate_methodology`). Non solleva: senza risposta, una metodologia vuota che lo dice."""
    from bookwriter.forge import quality as ql

    chiavi = ", ".join(c.key for c in ql.CRITERIA)
    sistema = (
        "Sei l'editor che decide COME analizzare questo testo, prima di analizzarlo. Leggi il "
        "profilo dell'opera, le regole dell'autore e il testo, e proponi la metodologia:\n"
        f"- 'criteria': i criteri che contano per questo tipo di opera e questo scopo, fra: "
        f"{chiavi}; con 'why' e un 'weight' (percento);\n"
        "- 'registers': le ENTITÀ di questo testo che vanno tracciate perché un'incoerenza "
        "romperebbe logica, continuità o payoff (es. schede di un archivio, nodi di una rete, "
        "copie di un documento, persone in un luogo): per ciascuna l'entità, l'unità, le "
        "categorie, se sono disgiunte ('disjoint') ed esaustive ('exhaustive'), il totale "
        "canonico se il testo o il canone lo fissano ('total', altrimenti null), lo stato "
        "iniziale, e gli invarianti che il codice verificherà fra: 'sum_to_total' (ogni "
        "distribuzione per categorie somma al totale — solo se disgiunte ed esaustive), "
        "'conservation' (presenti + rimossi − aggiunti = iniziali, nel tempo), 'single_place' "
        "(un elemento è in un solo luogo alla volta). Non tracciare ciò che non serve; non "
        "confondere entità diverse (una scheda non è un nodo);\n"
        "- 'motifs': i motivi da sottoporre ad audit, con alias (anche in altre lingue), "
        "significato e funzioni attese a ogni ricorrenza;\n"
        "- 'sources' le fonti canoniche da consultare, 'risks' i rischi specifici, 'tests' i "
        "test di accettazione (con il registro a cui si riferiscono, o vuoto), 'blocking' le "
        "condizioni che devono fermare il lavoro, 'author_questions' ciò che solo l'autore può "
        "decidere.\n"
        "La metodologia è una proposta subordinata al canone e alle regole dell'autore: non "
        f"inventare valori; un conflitto si segnala. Al più {MAX_REGISTERS} registri, "
        f"{MAX_MOTIFS} motivi, {MAX_TESTS} test."
    )
    utente = "\n\n".join(p for p in (
        f"# Il profilo dell'opera\n{profile_text(work)}",
        f"# Le regole dell'autore\n{rules_text(project)}" if project.get("rules") else "",
        f"# Il canone\n{canon}" if canon else "",
        f"# Lo scopo di questo passaggio\n{objective}",
        f"# Il testo ({scope}, capitolo {chapter})\n{text}") if p)
    metodo = Methodology(scope=scope, chapter=chapter,
                         created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         reason=objective)
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=_METHOD_SCHEMA, model=model)
    except Exception as exc:  # noqa: BLE001
        metodo.validation.append(f"metodologia non generata: {str(exc)[:160]}")
        return metodo
    return validate_methodology(r if isinstance(r, dict) else {}, metodo)


def validate_methodology(r: dict[str, Any], metodo: Methodology) -> Methodology:
    """Il codice controlla la proposta: invarianti di tipo noto, la somma solo con categorie
    disgiunte ed esaustive e un totale, i tetti; quello che toglie lo scrive in ``validation``."""
    from bookwriter.forge import quality as ql

    for c in r.get("criteria") or []:
        if isinstance(c, dict) and c.get("key") in ql.CRITERIA_BY_KEY:
            try:
                peso = max(0.0, float(c.get("weight") or 0))
            except (TypeError, ValueError):
                peso = 0.0
            metodo.criteria.append({"key": c["key"], "why": str(c.get("why") or ""),
                                    "weight": peso})
    visti: set[str] = set()
    for d in (r.get("registers") or [])[:MAX_REGISTERS + 3]:
        if not isinstance(d, dict):
            continue
        ident = re.sub(r"[^a-z0-9_]+", "_", str(d.get("id") or d.get("name") or "").lower()
                       ).strip("_")[:32]
        if not ident or ident in visti:
            continue
        try:
            totale = int(d["total"]) if d.get("total") not in (None, "") else None
        except (TypeError, ValueError):
            totale = None
        invarianti = [x for x in d.get("invariants") or [] if x in INVARIANT_KINDS]
        reg = Register(id=ident, name=str(d.get("name") or ident), purpose=str(d.get("purpose")
                       or ""), entity=str(d.get("entity") or ""), unit=str(d.get("unit") or ""),
                       categories=[str(x) for x in d.get("categories") or [] if str(x).strip()],
                       disjoint=bool(d.get("disjoint")), exhaustive=bool(d.get("exhaustive")),
                       total=totale, initial=str(d.get("initial") or ""),
                       invariants=invarianti, certainty=str(d.get("certainty") or ""))
        if "sum_to_total" in reg.invariants and not (reg.disjoint and reg.exhaustive
                                                     and reg.total is not None):
            reg.invariants.remove("sum_to_total")
            metodo.validation.append(
                f"«{reg.name}»: la somma al totale non si verifica (categorie non dichiarate "
                "disgiunte ed esaustive, o totale ignoto)")
        if not reg.invariants:
            metodo.validation.append(f"«{reg.name}»: tolto, nessun invariante verificabile")
            continue
        if len(metodo.registers) >= MAX_REGISTERS:
            metodo.validation.append(f"«{reg.name}»: tolto, oltre il tetto di {MAX_REGISTERS}")
            continue
        visti.add(ident)
        metodo.registers.append(reg)
    metodo.motifs = [{"id": str(m.get("id") or m.get("name") or ""), "name": str(m.get("name")
                      or ""), "aliases": [str(a) for a in m.get("aliases") or []],
                      "meaning": str(m.get("meaning") or ""),
                      "functions": [str(f) for f in m.get("functions") or []]}
                     for m in (r.get("motifs") or [])[:MAX_MOTIFS] if isinstance(m, dict)]
    metodo.sources = [str(x) for x in r.get("sources") or [] if str(x).strip()][:10]
    metodo.risks = [str(x) for x in r.get("risks") or [] if str(x).strip()][:10]
    metodo.tests = [{"id": str(t.get("id") or f"T{i}"), "description": str(t.get("description")
                     or ""), "register": str(t.get("register") or "")}
                    for i, t in enumerate((r.get("tests") or [])[:MAX_TESTS], 1)
                    if isinstance(t, dict)]
    metodo.blocking = [str(x) for x in r.get("blocking") or [] if str(x).strip()][:10]
    metodo.author_questions = [str(x) for x in r.get("author_questions") or []
                               if str(x).strip()][:10]
    return metodo


def methodology_dir(project) -> Path:
    return project.story_dir / "metodologia"


def save(project, metodo: Methodology, *, trial: bool = False) -> Path:
    """Salva la metodologia con un numero di versione nuovo; le precedenti restano."""
    cartella = methodology_dir(project)
    cartella.mkdir(parents=True, exist_ok=True)
    prefisso = f"{'prova_' if trial else ''}{metodo.chapter:02d}_{metodo.scope}"
    esistenti = sorted(cartella.glob(f"{prefisso}_v*.json"))
    metodo.version = len(esistenti) + 1
    path = cartella / f"{prefisso}_v{metodo.version}.json"
    path.write_text(json.dumps(asdict(metodo), ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_latest(project, chapter: int, scope: str, *, trial: bool = False) -> Methodology | None:
    cartella = methodology_dir(project)
    prefisso = f"{'prova_' if trial else ''}{chapter:02d}_{scope}"
    esistenti = sorted(cartella.glob(f"{prefisso}_v*.json"),
                       key=lambda p: int(re.search(r"_v(\d+)", p.stem).group(1)))
    if not esistenti:
        return None
    d = json.loads(esistenti[-1].read_text(encoding="utf-8"))
    metodo = Methodology(**{k: v for k, v in d.items() if k in Methodology.__dataclass_fields__
                            and k != "registers"})
    metodo.registers = [Register(**x) for x in d.get("registers") or []]
    return metodo


# --------------------------------------------------------------------------- #
#  F. l'estrazione
# --------------------------------------------------------------------------- #
_OBS_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"observations": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "register": _T,
            "kind": {"type": "string", "enum": ["distribution", "count", "location"]},
            "quote": _T, "time": _T,
            "parts": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"category": _T, "quantity": {"type": ["integer", "null"]}},
                "required": ["category", "quantity"]}},
            "total": {"type": ["integer", "null"]},
            "initial": {"type": ["integer", "null"]},
            "present": {"type": ["integer", "null"]},
            "removed": {"type": ["integer", "null"]},
            "added": {"type": ["integer", "null"]},
            "entity": _T, "place": _T,
            "certainty": {"type": "string", "enum": ["known", "ambiguous", "unknown"]}},
        "required": ["register", "kind", "quote", "time", "parts", "total", "initial", "present",
                     "removed", "added", "entity", "place", "certainty"]}}},
    "required": ["observations"],
}


@dataclass
class Observation:
    register: str
    section: int
    kind: str = ""
    quote: str = ""
    time: str = ""
    parts: list[tuple[str, int | None]] = field(default_factory=list)
    total: int | None = None
    initial: int | None = None
    present: int | None = None
    removed: int | None = None
    added: int | None = None
    entity: str = ""
    place: str = ""
    certainty: str = "known"


def _int(x: Any) -> int | None:
    try:
        return None if x in (None, "") else int(x)
    except (TypeError, ValueError):
        return None


def extract(client: Any, metodo: Methodology, sections: list[tuple[str, str]], *,
            model: str | None = None) -> tuple[list[Observation], list[str]]:
    """Le osservazioni dei registri, sezione per sezione, con la citazione. Ritorna anche gli
    errori (una sezione non letta si dice)."""
    if not metodo.registers:
        return [], []
    registri = "\n".join(
        f"- [{r.id}] {r.name}: {r.entity}" + (f"; categorie: {', '.join(r.categories)}"
                                              if r.categories else "")
        + (f"; totale canonico {r.total}" if r.total is not None else "")
        for r in metodo.registers)
    sistema = (
        "Sei il segretario di edizione. Riporta, per i registri dati, ogni AFFERMAZIONE di "
        "questa sezione che dice un numero o uno stato: 'distribution' (un totale diviso per "
        "categorie: le parti con la quantità), 'count' (quanti sono all'inizio 'initial', "
        "presenti 'present', rimossi 'removed', aggiunti 'added', e il totale se lo dice), "
        "'location' (dove si trova un elemento: 'entity', 'place'). Con 'quote' la frase esatta "
        "e 'time' il momento se il testo lo dice. Riporta i numeri COME LI DICE IL TESTO, anche "
        "se sono sbagliati: non correggere, non completare. Se un valore non si ricava, null; "
        "'certainty' 'ambiguous' o 'unknown' quando non sei sicuro di che cosa conti il testo."
    )
    osservazioni: list[Observation] = []
    errori: list[str] = []
    for i, (titolo, testo) in enumerate(sections):
        if not testo.strip():
            continue
        try:
            r = client.chat_json([{"role": "system", "content": sistema},
                                  {"role": "user", "content": f"# I registri\n{registri}\n\n"
                                                             f"# La sezione\n{titolo}\n\n{testo}"}],
                                 schema=_OBS_SCHEMA, model=model)
        except Exception as exc:  # noqa: BLE001
            errori.append(f"estrazione, «{titolo[:40]}»: {str(exc)[:120]}")
            continue
        for o in (r.get("observations") or []) if isinstance(r, dict) else []:
            if not isinstance(o, dict) or metodo.register(str(o.get("register") or "")) is None:
                continue
            osservazioni.append(Observation(
                register=str(o["register"]), section=i, kind=str(o.get("kind") or ""),
                quote=str(o.get("quote") or "")[:300], time=str(o.get("time") or ""),
                parts=[(str(p.get("category") or ""), _int(p.get("quantity")))
                       for p in o.get("parts") or [] if isinstance(p, dict)],
                total=_int(o.get("total")), initial=_int(o.get("initial")),
                present=_int(o.get("present")), removed=_int(o.get("removed")),
                added=_int(o.get("added")), entity=str(o.get("entity") or ""),
                place=str(o.get("place") or ""),
                certainty=str(o.get("certainty") or "known")))
    return osservazioni, errori


# --------------------------------------------------------------------------- #
#  G. il validatore
# --------------------------------------------------------------------------- #
@dataclass
class TestResult:
    test: str
    register: str
    outcome: str  #: pass | fail | ambiguous | not_run
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    sections: list[int] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.outcome == "fail"


def check(metodo: Methodology, osservazioni: list[Observation]) -> list[TestResult]:
    """Gli invarianti dei registri sulle osservazioni: solo conti, nessun giudizio."""
    esiti: list[TestResult] = []
    for reg in metodo.registers:
        mie = [o for o in osservazioni if o.register == reg.id]
        if "sum_to_total" in reg.invariants:
            esiti += _sum_to_total(reg, mie)
        if "conservation" in reg.invariants:
            esiti += _conservation(reg, mie)
        if "single_place" in reg.invariants:
            esiti += _single_place(reg, mie)
    return esiti


def _sum_to_total(reg: Register, mie: list[Observation]) -> list[TestResult]:
    esiti = []
    distribuzioni = [o for o in mie if o.kind == "distribution" and o.parts]
    if not distribuzioni:
        return [TestResult(f"{reg.id}.sum", reg.id, "not_run",
                           "nessuna distribuzione nel testo")]
    for o in distribuzioni:
        quantita = [q for _c, q in o.parts]
        atteso = reg.total if reg.total is not None else o.total
        if any(q is None for q in quantita) or atteso is None or o.certainty != "known":
            esiti.append(TestResult(f"{reg.id}.sum", reg.id, "ambiguous",
                                    "una parte o il totale non si ricava dal testo",
                                    [o.quote], [o.section]))
            continue
        somma = sum(quantita)
        dettaglio = " + ".join(f"{q} ({c})" for c, q in o.parts) + f" = {somma}"
        if somma != atteso:
            esiti.append(TestResult(f"{reg.id}.sum", reg.id, "fail",
                                    f"{reg.name}: {dettaglio}, ma il totale è {atteso}",
                                    [o.quote], [o.section]))
        else:
            esiti.append(TestResult(f"{reg.id}.sum", reg.id, "pass", dettaglio, [o.quote],
                                    [o.section]))
    return esiti


def _conservation(reg: Register, mie: list[Observation]) -> list[TestResult]:
    """I conteggi nel tempo: un iniziale solo; presenti = iniziali − rimossi + aggiunti; un
    conteggio presente non cambia senza un evento che lo spieghi."""
    conti = [o for o in mie if o.kind == "count" and o.certainty == "known"]
    if not conti:
        return [TestResult(f"{reg.id}.conservation", reg.id, "not_run", "nessun conteggio")]
    esiti = []
    iniziali = {(o.initial if o.initial is not None else None) for o in conti} - {None}
    if reg.total is not None and any(o.initial not in (None, reg.total) for o in conti):
        iniziali.add(reg.total)
    if len(iniziali) > 1:
        prove = [o for o in conti if o.initial is not None]
        esiti.append(TestResult(f"{reg.id}.initial", reg.id, "fail",
                                f"{reg.name}: il numero iniziale cambia "
                                f"({', '.join(map(str, sorted(iniziali)))})",
                                [o.quote for o in prove][:4], [o.section for o in prove][:4]))
    corrente: int | None = reg.total if reg.total is not None else \
        (min(iniziali) if len(iniziali) == 1 else None)
    precedente: Observation | None = None
    for o in conti:
        if o.initial is not None and corrente is None:
            corrente = o.initial
        if corrente is not None and (o.removed is not None or o.added is not None):
            atteso = corrente - (o.removed or 0) + (o.added or 0)
            if o.present is not None and o.present != atteso:
                esiti.append(TestResult(f"{reg.id}.conservation", reg.id, "fail",
                                        f"{reg.name}: {corrente} − {o.removed or 0} + "
                                        f"{o.added or 0} = {atteso}, ma il testo dice "
                                        f"{o.present} presenti", [o.quote], [o.section]))
            corrente = o.present if o.present is not None else atteso
        elif o.present is not None:
            if corrente is not None and o.present != corrente:
                esiti.append(TestResult(
                    f"{reg.id}.conservation", reg.id, "fail",
                    f"{reg.name}: {o.present} presenti senza un evento che lo spieghi (prima "
                    f"{corrente})", [x.quote for x in (precedente, o) if x], [o.section]))
            corrente = o.present
        precedente = o
    if not any(e.failed for e in esiti):
        esiti.append(TestResult(f"{reg.id}.conservation", reg.id, "pass",
                                f"{len(conti)} conteggi coerenti"))
    return esiti


def _single_place(reg: Register, mie: list[Observation]) -> list[TestResult]:
    luoghi: dict[tuple[str, int, str], set[str]] = {}
    prove: dict[tuple[str, int, str], list[str]] = {}
    for o in mie:
        if o.kind != "location" or not o.entity or not o.place or o.certainty != "known":
            continue
        chiave = (o.entity.lower(), o.section, o.time.lower())
        luoghi.setdefault(chiave, set()).add(o.place.lower())
        prove.setdefault(chiave, []).append(o.quote)
    esiti = [TestResult(f"{reg.id}.place", reg.id, "fail",
                        f"«{k[0]}» è in {len(v)} luoghi nello stesso momento: {', '.join(v)}",
                        prove[k][:3], [k[1]])
             for k, v in luoghi.items() if len(v) > 1]
    return esiti or [TestResult(f"{reg.id}.place", reg.id,
                                "pass" if luoghi else "not_run",
                                "un luogo alla volta" if luoghi else "nessuna posizione")]


def failures_text(esiti: list[TestResult]) -> list[str]:
    """I test falliti come difetti bloccanti, con la prova."""
    return [f"[blocker] registro «{e.register}»: {e.detail}"
            + (f" — «{e.evidence[0][:160]}»" if e.evidence and e.evidence[0] else "")
            for e in esiti if e.failed]
