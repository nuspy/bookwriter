"""Le affermazioni della ricerca, una volta sola ciascuna, con le loro fonti vere.

La ricerca di un libro produce centinaia di «finding» capitolo per capitolo: la stessa notizia
ritorna da dieci pagine che si copiano a vicenda, lo stesso fatto si trova sotto tre punti, e
qualche fonte il modello l'ha scritta senza averla letta (in *The Red Thread*, 74 affermazioni su
663 citavano indirizzi mai scaricati, né comparsi fra i risultati). Dieci pagine non sono dieci
conferme, e un indirizzo inventato non è una fonte.

Qui il materiale diventa un **archivio di affermazioni** (``research/claims.json``):

* ogni affermazione una volta sola, con le varianti che la sostengono raccolte insieme — ma «un
  documento menziona l'oggetto» e «l'oggetto è autentico» restano due affermazioni;
* le fonti con la loro **indipendenza**: le pagine dello stesso sito, o quasi identiche fra loro
  (vedi :func:`page_groups`), sono una sola linea d'informazione;
* la fonte **verificata** solo se la pagina è stata letta davvero; le altre si dicono non verificate
  e non entrano in bibliografia;
* il tipo (fatto storico, tradizione, interpretazione, ipotesi), il livello di sostegno, le
  decisioni dell'autore sui contrasti, i capitoli in cui è già stata usata.

Il livello di sostegno è un'**informazione**, non un filtro: il libro sostiene le tesi
dell'autore, e un'affermazione debole resta materiale buono se serve alla scena. Quello che cambia
è che la stesura riceve poche affermazioni scelte per il capitolo (:func:`select_for_chapter`),
invece dei primi novemila caratteri del file di ricerca.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bookwriter.forge import research_store as store
from bookwriter.forge.metrics import _cos, _stem, content_words, shingles
from bookwriter.forge.project import BookProject, ForgeState
from bookwriter.forge.websearch import norm_url

#: Il tipo di un'affermazione: dice di che cosa si tratta, non se è vera.
CLAIM_TYPES = ("fatto_storico", "tradizione", "interpretazione", "ipotesi", "fiction_canonica",
               "contrasto")

#: Le affermazioni che non portano materiale ma obiettano alla tesi dell'autore («non va presentato
#: come un fatto», «non è attestato»). Non sono materiale per la scena: sono contrasti, e dei
#: contrasti decide l'autore (le sue decisioni arrivano alla stesura per un'altra via).
_OBIEZIONE = re.compile(
    r"non va (?:presentat|trattat)|non (?:è|e') (?:attestat|documentat|provat|verificabil)|"
    r"non (?:sono|è) (?:supportat|confermat)|leggenda tarda|privo di (?:prove|fondamento)|"
    r"not (?:be )?(?:presented|attested|documented|supported|verifiable)|no (?:archaeological )?"
    r"evidence|is a (?:later )?(?:myth|legend)|unsubstantiated|lacks evidence", re.IGNORECASE)

#: Il sostegno di un'affermazione, dal più al meno solido. Non toglie mai niente all'autore.
SUPPORT = ("forte", "moderato", "debole", "controverso", "non_verificato")

#: Due pagine con almeno questa parte di frasi in comune sono la stessa pagina (copia, mirror).
NEAR_DUPLICATE = 0.8

#: Due affermazioni così simili nelle parole sono candidate a essere la stessa (si chiede conferma).
SAME_CLAIM_CANDIDATE = 0.55
#: Senza un modello a confermare, si fondono solo quelle praticamente identiche.
SAME_CLAIM_CERTAIN = 0.9


@dataclass
class Source:
    id: str = ""
    url: str = ""
    title: str = ""
    site: str = ""
    author: str = ""
    published: str = ""
    accessed: str = ""
    level: str = ""
    #: la pagina è stata scaricata e letta: solo allora la fonte è verificata
    read: bool = False
    #: il gruppo d'indipendenza: stesso sito o pagine quasi identiche = stesso gruppo
    group: str = ""
    #: la pagina (url normalizzato) di cui questa è quasi una copia
    duplicate_of: str = ""


@dataclass
class Claim:
    id: str = ""
    text: str = ""
    detail: str = ""
    use: str = ""
    #: il punto della struttura per cui è stata trovata
    topic: str = ""
    chapter: int = 0
    point: int = 0
    claim_type: str = ""
    source_ids: list[str] = field(default_factory=list)
    #: i gruppi d'indipendenza delle fonti verificate
    groups: list[str] = field(default_factory=list)
    level: str = ""
    support: str = ""
    verified: bool = False
    counterclaims: list[str] = field(default_factory=list)
    author_decision: str = ""
    #: i capitoli che l'hanno già usata (dai piani delle scene)
    chapter_usage: list[int] = field(default_factory=list)
    last_used_scene: str = ""
    #: ``libera`` · ``nuova_funzione`` (già usata: torna solo se fa un lavoro nuovo)
    reuse_rule: str = "libera"
    #: le altre formulazioni fuse in questa: (capitolo, punto, testo)
    variants: list[str] = field(default_factory=list)
    #: i capitoli in cui la ricerca l'ha trovata
    found_in: list[int] = field(default_factory=list)


@dataclass
class ClaimStore:
    claims: list[Claim] = field(default_factory=list)
    sources: dict[str, Source] = field(default_factory=dict)
    #: quanto materiale c'era prima della normalizzazione: per il resoconto
    stats: dict[str, int] = field(default_factory=dict)

    def claim(self, ident: str) -> Claim | None:
        return next((c for c in self.claims if c.id == ident), None)

    def for_chapter(self, number: int) -> list[Claim]:
        return [c for c in self.claims if number in c.found_in]


# --------------------------------------------------------------------------- #
#  su disco
# --------------------------------------------------------------------------- #
def store_path(project: BookProject) -> Path:
    return project.research_dir / "claims.json"


def save(project: BookProject, archivio: ClaimStore) -> Path:
    path = store_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    dati = {"stats": archivio.stats,
            "sources": {k: asdict(v) for k, v in sorted(archivio.sources.items())},
            "claims": [asdict(c) for c in archivio.claims]}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load(project: BookProject) -> ClaimStore | None:
    path = store_path(project)
    if not path.exists():
        return None
    try:
        dati = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    fonti = {k: Source(**{c: v for c, v in s.items() if c in Source.__dataclass_fields__})
             for k, s in (dati.get("sources") or {}).items() if isinstance(s, dict)}
    affermazioni = [Claim(**{k: v for k, v in c.items() if k in Claim.__dataclass_fields__})
                    for c in (dati.get("claims") or []) if isinstance(c, dict)]
    return ClaimStore(affermazioni, fonti, dict(dati.get("stats") or {}))


# --------------------------------------------------------------------------- #
#  fonti e indipendenza
# --------------------------------------------------------------------------- #
def site_of(url: str) -> str:
    """Il sito di un indirizzo, senza ``www.`` né sottodomini linguistici (``en.wikipedia.org``
    e ``it.wikipedia.org`` sono la stessa enciclopedia)."""
    host = (urlsplit(url or "").hostname or "").lower()
    host = re.sub(r"^(?:www\d?|m|amp)\.", "", host)
    parti = host.split(".")
    if len(parti) > 2 and len(parti[0]) == 2:  # en.wikipedia.org → wikipedia.org
        parti = parti[1:]
    return ".".join(parti)


def source_id(url: str, title: str = "") -> str:
    chiave = norm_url(url) if url else "senza-indirizzo:" + (title or "").strip().lower()
    return "S" + hashlib.sha1(chiave.encode("utf-8")).hexdigest()[:10]


class _Unione:
    def __init__(self) -> None:
        self.padre: dict[str, str] = {}

    def trova(self, x: str) -> str:
        self.padre.setdefault(x, x)
        while self.padre[x] != x:
            self.padre[x] = self.padre[self.padre[x]]
            x = self.padre[x]
        return x

    def unisci(self, a: str, b: str) -> None:
        ra, rb = self.trova(a), self.trova(b)
        if ra != rb:
            self.padre[max(ra, rb)] = min(ra, rb)


def page_groups(pages: dict[str, str], sites: dict[str, str] | None = None,
                threshold: float = NEAR_DUPLICATE) -> tuple[dict[str, str], dict[str, str]]:
    """I gruppi d'indipendenza delle pagine lette: ``(gruppo di ogni url, copia_di)``.

    ``pages`` è url normalizzato → testo. Due pagine dello stesso sito, o quasi identiche
    (Jaccard delle sequenze di cinque parole ≥ ``threshold``), stanno nello stesso gruppo. Il
    confronto è a coppie solo fra pagine che condividono almeno una sequenza rara: le altre non
    possono essere copie.
    """
    unione = _Unione()
    copie: dict[str, str] = {}
    per_sito: dict[str, str] = {}
    for url in pages:
        sito = (sites or {}).get(url) or site_of(url)
        unione.trova(url)
        if sito:
            if sito in per_sito:
                unione.unisci(url, per_sito[sito])
            else:
                per_sito[sito] = url
    insiemi = {u: shingles(t) for u, t in pages.items() if t}
    indice: dict[str, list[str]] = defaultdict(list)
    for u, s in insiemi.items():
        for sh in list(s)[:400]:
            indice[sh].append(u)
    candidati: set[tuple[str, str]] = set()
    for gruppo in indice.values():
        if 1 < len(gruppo) <= 20:
            for i, a in enumerate(gruppo):
                for b in gruppo[i + 1:]:
                    candidati.add((min(a, b), max(a, b)))
    for a, b in sorted(candidati):
        sa, sb = insiemi[a], insiemi[b]
        if sa and sb and len(sa & sb) / len(sa | sb) >= threshold:
            unione.unisci(a, b)
            copie.setdefault(b, a)
    return {u: "G" + hashlib.sha1(unione.trova(u).encode()).hexdigest()[:8] for u in pages}, copie


class PageIndex:
    """Le pagine già lette in tutto il libro, per riconoscere una copia prima di leggerla di nuovo.

    Una pagina che riproduce un'altra già letta (un mirror, un articolo ripreso parola per parola)
    non porta niente di nuovo: estrarne di nuovo le affermazioni crea solo l'impressione di una
    conferma in più. La si registra come copia e non va al modello.
    """

    def __init__(self, threshold: float = NEAR_DUPLICATE) -> None:
        self.threshold = threshold
        self._insiemi: dict[str, set[str]] = {}
        self._indice: dict[str, list[str]] = defaultdict(list)

    @classmethod
    def from_chapters(cls, capitoli: list[store.ChapterData]) -> PageIndex:
        indice = cls()
        for dati in capitoli:
            for punto in dati.points:
                for pg in punto.pages:
                    if pg.status == "" and pg.text and not pg.discarded:
                        indice.add(pg.url, pg.text)
        return indice

    def add(self, url: str, text: str) -> None:
        chiave = norm_url(url)
        if chiave in self._insiemi or not text:
            return
        s = shingles(text)
        self._insiemi[chiave] = s
        for sh in sorted(s, key=hash)[:300]:
            self._indice[sh].append(chiave)

    def duplicate_of(self, url: str, text: str) -> str:
        """L'indirizzo della pagina già letta di cui questa è una copia, o vuoto."""
        chiave = norm_url(url)
        s = shingles(text)
        if not s:
            return ""
        candidati: Counter[str] = Counter()
        for sh in sorted(s, key=hash)[:300]:
            for altro in self._indice.get(sh, ()):
                if altro != chiave:
                    candidati[altro] += 1
        for altro, _ in candidati.most_common(5):
            t = self._insiemi[altro]
            if len(s & t) / len(s | t) >= self.threshold:
                return altro
        return ""


# --------------------------------------------------------------------------- #
#  costruire l'archivio
# --------------------------------------------------------------------------- #
def _norm(testo: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (testo or "").lower())).strip()


def claim_id(testo: str) -> str:
    return "K" + hashlib.sha1(_norm(testo).encode("utf-8")).hexdigest()[:10]


def _vettore(testo: str) -> Counter[str]:
    return Counter(_stem(w) for w in content_words(testo))


def _tfidf(sacchi: list[Counter[str]]) -> list[dict[str, float]]:
    df: Counter[str] = Counter()
    for s in sacchi:
        df.update(s.keys())
    n = len(sacchi)
    fuori = []
    for s in sacchi:
        v = {t: c * (math.log((1 + n) / (1 + df[t])) + 1) for t, c in s.items()}
        norma = math.sqrt(sum(x * x for x in v.values())) or 1.0
        fuori.append({t: x / norma for t, x in v.items()})
    return fuori


def _support(c: Claim, fonti: dict[str, Source]) -> str:
    if not c.verified:
        return "non_verificato"
    if c.counterclaims:
        return "controverso"
    livelli = {fonti[s].level for s in c.source_ids if s in fonti and fonti[s].read}
    gruppi = len(set(c.groups))
    if gruppi >= 2 and livelli & {"A", "B"}:
        return "forte"
    if gruppi >= 2 or livelli & {"A", "B"}:
        return "moderato"
    return "debole"


_TYPE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"n": {"type": "integer"}, "type": {"type": "string"}},
        "required": ["n", "type"]}}},
    "required": ["items"],
}

_SAME_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"same": {"type": "array", "items": {"type": "integer"}}},
    "required": ["same"],
}


def _confirm_same(client: Any, coppie: list[tuple[Claim, Claim]], *, model: str | None
                  ) -> set[int]:
    """Quali coppie dicono davvero la stessa cosa: lo decide il modello, a lotti."""
    stesse: set[int] = set()
    sistema = (
        "Ti do coppie di affermazioni ricavate da fonti diverse. Per ogni coppia decidi se "
        "affermano LA STESSA COSA, così che una possa stare al posto dell'altra. Attenzione: "
        "«un documento menziona X» non è «X è autentico»; «la tradizione dice X» non è «X è "
        "accaduto»; date, numeri o luoghi diversi fanno affermazioni diverse. Nel dubbio, non "
        "sono la stessa. Rispondi con 'same': i numeri delle coppie che sono la stessa "
        "affermazione (elenco vuoto se nessuna)."
    )
    for inizio in range(0, len(coppie), 20):
        lotto = coppie[inizio:inizio + 20]
        utente = "\n".join(f"{inizio + i + 1}. A: {a.text} — B: {b.text}"
                           for i, (a, b) in enumerate(lotto))
        try:
            r = client.chat_json([{"role": "system", "content": sistema},
                                  {"role": "user", "content": utente}],
                                 schema=_SAME_SCHEMA, model=model)
            stesse.update(int(n) - 1 for n in (r.get("same") or [])
                          if isinstance(n, int) and inizio < n <= inizio + len(lotto))
        except Exception:  # noqa: BLE001, S112 - un lotto non confermato non si fonde: è la scelta prudente
            continue
    return stesse


def _classify(client: Any, affermazioni: list[Claim], *, model: str | None) -> None:
    sistema = (
        "Classifica ogni affermazione per tipo, senza giudicare se è vera: 'fatto_storico' "
        "(evento, data, luogo, persona documentati come tali), 'tradizione' (ciò che una "
        "tradizione, una leggenda o un rito tramanda), 'interpretazione' (la lettura che un "
        "autore dà di un fatto o di un simbolo), 'ipotesi' (una congettura dichiarata), "
        "'contrasto' (non porta materiale: obietta che qualcosa non è attestato, non va "
        "presentato come fatto, è una leggenda). "
        "Rispondi con 'items': per ogni numero il tipo."
    )
    for inizio in range(0, len(affermazioni), 25):
        lotto = affermazioni[inizio:inizio + 25]
        utente = "\n".join(f"{i + 1}. {c.text}" for i, c in enumerate(lotto))
        try:
            r = client.chat_json([{"role": "system", "content": sistema},
                                  {"role": "user", "content": utente}],
                                 schema=_TYPE_SCHEMA, model=model)
        except Exception:  # noqa: BLE001, S112 - senza tipo resta senza tipo
            continue
        for voce in r.get("items") or []:
            try:
                n, tipo = int(voce.get("n")) - 1, str(voce.get("type") or "")
            except (TypeError, ValueError):
                continue
            if 0 <= n < len(lotto) and tipo in CLAIM_TYPES:
                lotto[n].claim_type = tipo


def _usage(project: BookProject) -> dict[str, tuple[list[int], str]]:
    """Dai piani delle scene: quali affermazioni ogni capitolo ha già usato, e in che scena."""
    usate: dict[str, tuple[list[int], str]] = {}
    cartella = project.plans_dir
    if not cartella.exists():
        return usate
    for path in sorted(cartella.glob("*.json")):
        try:
            piano = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if piano.get("trial"):
            continue  # una prova non è il libro
        numero = int(piano.get("chapter") or 0)
        for scena in piano.get("scenes") or []:
            for k in scena.get("claim_ids") or []:
                capitoli, _ = usate.get(k, ([], ""))
                if numero not in capitoli:
                    capitoli = [*capitoli, numero]
                usate[k] = (capitoli, f"{numero}.{scena.get('id', '')}")
    return usate


def build(project: BookProject, state: ForgeState, client: Any = None, *,
          model: str | None = None, progress=None) -> ClaimStore:
    """Costruisce l'archivio dai dati della ricerca di tutti i capitoli. Non modifica i file della
    ricerca: scrive solo ``research/claims.json``."""
    capitoli = store.load_all_chapters(project.research_dir, state.outline)
    fonti: dict[str, Source] = {}
    testi: dict[str, str] = {}
    viste: set[str] = set()
    for dati in capitoli:
        for punto in dati.points:
            for h in punto.results:
                viste.add(norm_url(h.url))
            for pg in punto.pages:
                if pg.discarded or not pg.url:
                    continue
                chiave = norm_url(pg.url)
                letta = pg.status == "" and bool(pg.text)
                sid = source_id(pg.url)
                if sid not in fonti:
                    fonti[sid] = Source(id=sid, url=pg.url, title=pg.title, site=site_of(pg.url),
                                        accessed=dati.date, read=letta,
                                        author=getattr(pg, "author", "") or "",
                                        published=getattr(pg, "published", "") or "")
                elif letta:
                    fonti[sid].read = True
                if letta and pg.text:
                    testi.setdefault(chiave, pg.text)
    gruppo_di, copia_di = page_groups(testi)
    for s in fonti.values():
        chiave = norm_url(s.url)
        s.group = gruppo_di.get(chiave, "")
        if chiave in copia_di:
            s.duplicate_of = copia_di[chiave]

    grezze: list[Claim] = []
    domande: list[tuple[int, int, store.Question]] = []
    for dati in capitoli:
        for q in dati.questions:
            if not q.discarded:
                domande.append((dati.number, q.point, q))
        for punto in dati.points:
            for f in punto.findings:
                if f.excluded or not f.claim.strip():
                    continue
                sid = source_id(f.url, f.source) if (f.url or f.source) else ""
                letta = False
                if sid:
                    if sid not in fonti:
                        fonti[sid] = Source(id=sid, url=f.url, title=f.source,
                                            site=site_of(f.url), accessed=dati.date,
                                            read=False)
                    s = fonti[sid]
                    letta = s.read
                    if f.level and not s.level:
                        s.level = f.level
                    if not s.title and f.source:
                        s.title = f.source
                grezze.append(Claim(
                    id=claim_id(f.claim), text=f.claim.strip(), detail=f.detail.strip(),
                    use=f.use.strip(), topic=punto.text, chapter=dati.number,
                    point=punto.index, source_ids=[sid] if sid else [],
                    groups=[fonti[sid].group] if sid and letta and fonti[sid].group else [],
                    level=f.level, verified=letta, found_in=[dati.number]))

    # le affermazioni equivalenti si fondono: prima le identiche, poi le confermate
    vettori = _tfidf([_vettore(c.text + " " + c.detail[:200]) for c in grezze])
    unione = _Unione()
    coppie: list[tuple[int, int]] = []
    for i in range(len(grezze)):
        unione.trova(str(i))
        for j in range(i + 1, len(grezze)):
            if _norm(grezze[i].text) == _norm(grezze[j].text):
                unione.unisci(str(i), str(j))
                continue
            s = _cos(vettori[i], vettori[j])
            if s >= SAME_CLAIM_CERTAIN:
                unione.unisci(str(i), str(j))
            elif s >= SAME_CLAIM_CANDIDATE:
                coppie.append((i, j))
    if client is not None and coppie:
        confermate = _confirm_same(client, [(grezze[i], grezze[j]) for i, j in coppie],
                                   model=model)
        for k in confermate:
            unione.unisci(str(coppie[k][0]), str(coppie[k][1]))
    gruppi: dict[str, list[Claim]] = defaultdict(list)
    for i, c in enumerate(grezze):
        gruppi[unione.trova(str(i))].append(c)

    usate = _usage(project)
    affermazioni: list[Claim] = []
    for membri in gruppi.values():
        # la formulazione con la fonte letta e più dettaglio rappresenta il gruppo
        membri.sort(key=lambda c: (not c.verified, -len(c.detail)))
        capo = membri[0]
        for altro in membri[1:]:
            capo.variants.append(f"cap. {altro.chapter}: {altro.text}")
            capo.source_ids += [s for s in altro.source_ids if s not in capo.source_ids]
            capo.groups += [g for g in altro.groups if g not in capo.groups]
            capo.found_in += [n for n in altro.found_in if n not in capo.found_in]
            capo.verified = capo.verified or altro.verified
            if altro.level and (not capo.level or altro.level < capo.level):
                capo.level = altro.level
        capitoli_uso, scena = usate.get(capo.id, ([], ""))
        capo.chapter_usage, capo.last_used_scene = capitoli_uso, scena
        capo.reuse_rule = "nuova_funzione" if capitoli_uso else "libera"
        affermazioni.append(capo)

    _attach_questions(affermazioni, domande)
    if client is not None and affermazioni:
        _classify(client, affermazioni, model=model)
    for c in affermazioni:
        if _OBIEZIONE.search(f"{c.text} {c.detail[:300]}"):
            c.claim_type = "contrasto"
        if not c.claim_type:
            c.claim_type = {"E": "ipotesi", "D": "tradizione"}.get(c.level, "fatto_storico")
        c.support = _support(c, fonti)

    archivio = ClaimStore(affermazioni, fonti, {
        "finding": len(grezze), "affermazioni": len(affermazioni),
        "fonti": len(fonti), "fonti_lette": sum(1 for s in fonti.values() if s.read),
        "fonti_non_verificate": sum(1 for s in fonti.values() if not s.read),
        "gruppi_indipendenti": len({s.group for s in fonti.values() if s.group}),
        "pagine_quasi_copia": len(copia_di),
        "coppie_candidate": len(coppie),
    })
    save(project, archivio)
    if progress:
        progress(1, 1)
    return archivio


def _attach_questions(affermazioni: list[Claim],
                      domande: list[tuple[int, int, store.Question]]) -> None:
    """I contrasti segnalati e le decisioni dell'autore vanno sull'affermazione a cui si
    riferiscono: la più vicina nelle parole fra quelle dello stesso punto."""
    for numero, punto, q in domande:
        candidati = [c for c in affermazioni if c.chapter == numero and c.point == punto] or \
                    [c for c in affermazioni if c.chapter == numero]
        if not candidati:
            continue
        vq = _vettore(q.text)
        migliore = max(candidati, key=lambda c: _cos(_unit(vq), _unit(_vettore(c.text))))
        if q.outcome.get("kind") != store.OUT_SOURCE and q.text not in migliore.counterclaims:
            migliore.counterclaims.append(q.text)
        esito = q.outcome or {}
        if esito.get("kind") == store.OUT_PUBLISH:
            migliore.author_decision = f"pubblica così: {esito.get('text', '')}"
        elif esito.get("kind") == store.OUT_OMIT:
            migliore.author_decision = "non menzionare"
        elif esito.get("kind") == store.OUT_SOURCE:
            migliore.author_decision = f"fonte trovata: {esito.get('source') or esito.get('url')}"


def _unit(v: Counter[str]) -> dict[str, float]:
    norma = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norma for t, x in v.items()}


# --------------------------------------------------------------------------- #
#  scegliere per un capitolo
# --------------------------------------------------------------------------- #
@dataclass
class Selection:
    chosen: list[Claim] = field(default_factory=list)
    #: pertinenti, ma rinviate: restano nell'archivio
    deferred: list[Claim] = field(default_factory=list)


_SUPPORT_SCORE = {"forte": 1.0, "moderato": 0.8, "controverso": 0.6, "debole": 0.6,
                  "non_verificato": 0.3}


def select_for_chapter(archivio: ClaimStore, chapter: dict[str, Any], *,
                       limit: int = 12, minimum: int = 5) -> Selection:
    """Le affermazioni utili a un capitolo: poche, scelte, distribuite fra i suoi punti.

    Pesa la pertinenza al capitolo (parole del suo «does» e dei punti), il sostegno (una fonte
    letta vale più di una non verificata), e penalizza quelle già usate da un capitolo
    precedente: possono tornare, ma solo con una funzione nuova, e lo decide il piano.
    """
    numero = int(chapter.get("number") or 0)
    punti = [str(p) for p in (chapter.get("points") or [])]
    bersaglio = _unit(_vettore(" ".join([str(chapter.get("does") or ""), *punti])))
    candidati = [c for c in archivio.claims
                 if c.author_decision != "non menzionare" and c.claim_type != "contrasto"]
    punteggi: dict[str, float] = {}
    for c in candidati:
        pertinenza = _cos(bersaglio, _unit(_vettore(f"{c.text} {c.use} {c.topic}")))
        if numero in c.found_in:
            pertinenza += 0.5  # trovata proprio per questo capitolo
        gia = [n for n in c.chapter_usage if n != numero]
        penalita = 0.5 if gia else 1.0
        punteggi[c.id] = pertinenza * _SUPPORT_SCORE.get(c.support, 0.5) * penalita
    ordinati = sorted((c for c in candidati if punteggi[c.id] > 0.05),
                      key=lambda c: -punteggi[c.id])
    # un giro per punto, così che ogni punto abbia il suo materiale, poi per punteggio
    scelte: list[Claim] = []
    per_punto: dict[int, list[Claim]] = defaultdict(list)
    for c in ordinati:
        if numero in c.found_in and c.chapter == numero:
            per_punto[c.point].append(c)
    for giro in range(3):
        for indice in sorted(per_punto):
            if len(per_punto[indice]) > giro and len(scelte) < limit:
                c = per_punto[indice][giro]
                if c not in scelte:
                    scelte.append(c)
    for c in ordinati:  # se i punti del capitolo ne danno poche, si completa con le migliori
        if len(scelte) >= minimum:
            break
        if c not in scelte:
            scelte.append(c)
    rinviate = [c for c in ordinati if c not in scelte][:20]
    return Selection(scelte[:limit], rinviate)


def claim_line(c: Claim, fonti: dict[str, Source]) -> str:
    """Una riga per il piano e per la stesura: id, affermazione, dettaglio, fonte, sostegno."""
    fonte = next((fonti[s] for s in c.source_ids if s in fonti), None)
    dove = (fonte.title or fonte.site) if fonte else "senza fonte"
    stato = "fonte letta" if c.verified else "fonte NON verificata: non citarla"
    riga = f"[{c.id}] ({c.claim_type or '?'}, {c.support or '?'}; {dove}, {stato}) {c.text}"
    if c.detail:
        riga += f" — {c.detail[:400]}"
    if c.author_decision:
        riga += f" — DECISIONE DELL'AUTORE: {c.author_decision}"
    if c.chapter_usage:
        riga += f" — già usata nei capitoli {', '.join(map(str, c.chapter_usage))}"
    return riga


_ACCESSED = {"it": "consultato il", "en": "accessed", "fr": "consulté le", "de": "abgerufen am",
             "es": "consultado el", "pt": "acessado em"}


def bibliography(archivio: ClaimStore, ids: list[str], lang: str = "it") -> list[str]:
    """Le voci di bibliografia per le affermazioni usate: solo fonti lette davvero, una volta."""
    viste: set[str] = set()
    voci: list[str] = []
    for ident in ids:
        c = archivio.claim(ident)
        if c is None:
            continue
        for sid in c.source_ids:
            s = archivio.sources.get(sid)
            if s is None or not s.read or s.id in viste or not s.url:
                continue
            viste.add(s.id)
            parti = [p for p in (s.author, s.title or s.site) if p]
            voce = ", ".join(f"“{p}”" if p == s.title else p for p in parti) or s.site
            if s.published:
                voce += f" ({s.published})"
            voce += f". {s.url}"
            if s.accessed:
                voce += f" — {_ACCESSED.get(lang, _ACCESSED['en'])} {s.accessed}"
            voci.append(voce)
    return voci
