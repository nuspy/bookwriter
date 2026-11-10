"""Le misure di un capitolo che non chiedono niente a un modello.

Un capitolo che ripete la stessa tesi con parole diverse non ha paragrafi identici: il controllo
letterale (:func:`bookwriter.llm.jsoncheck.repeated_text`) lo lascia passare intero. Il capitolo 11
di *The Red Thread* diceva «availability was danger» in quaranta modi, con «danger» 81 volte,
«committee» 66 e il protagonista nominato 4: numeri che si contano, senza giudizio.

Queste misure **non vietano niente**. Una parola chiave può tornare cento volte se ogni volta fa
un lavoro diverso; il protagonista di un episodio storico può non comparire. Servono a dire
all'editor (:mod:`bookwriter.forge.editor`) *dove guardare*: quali paragrafi si assomigliano,
quali parole si addensano oltre la misura del libro, quali formule si ripetono in serie. Il
giudizio resta a lui, e all'autore.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

#: Parole che non portano significato: senza toglierle, due paragrafi qualunque si somigliano.
STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had
has have having he her here hers herself him himself his how i if in into is it its itself just
me more most my myself no nor not now of off on once only or other our ours ourselves out over
own same she should so some such than that the their theirs them themselves then there these
they this those through to too under until up very was we were what when where which while who
whom why will with would you your yours yourself yourselves one ones upon into onto like even
still yet ever never every much many another though although whether within without toward
towards across along around among
must shall might may need needs
il lo la i gli le un uno una di da in con su per tra fra a e o ma se che chi cui non ne ci vi si
del dello della dei degli delle al allo alla ai agli alle dal dallo dalla dai dagli dalle nel
nello nella nei negli nelle sul sullo sulla sui sugli sulle col coi è era erano sono essere stato
stata stati state ha hanno aveva avevano avere come anche più già poi però quando dove questo
questa questi queste quello quella quelli quelle suo sua suoi sue loro mio mia tuo tua nostro
nostra ogni tutto tutti tutta tutte molto poco così solo ancora sempre mai
""".split())

#: Le formule retoriche che, ripetute in serie, fanno della prosa un'eco. In inglese e in italiano.
FORMULAS: list[tuple[str, re.Pattern[str]]] = [
    ("«non X. È Y»",
     re.compile(r"\b(?:was|is|were|are|had)\s+not\b[^.!?]{0,80}[.!?]\s+(?:It|He|She|They|This|That)"
                r"\s+(?:was|is|were|are|had)\b|\bnon\s+(?:era|è|erano|sono)\b[^.!?]{0,80}[.!?]\s+"
                r"(?:Era|È|Erano|Sono)\b", re.IGNORECASE)),
    ("«non X, ma Y»",
     re.compile(r"\b(?:not|no longer)\s+(?:a|an|the)?\s*\w+(?:\s+\w+){0,4},?\s+but\b|\bnon\s+"
                r"(?:un|una|il|la|lo)?\s*\w+(?:\s+\w+){0,4},?\s+ma\b", re.IGNORECASE)),
    ("catena «A era B. B era C»",
     re.compile(r"\bThe\s+(\w+)\s+(?:was|had)\s+(?:the\s+)?(\w+)[^.]{0,40}\.\s+The\s+\2\b",
                re.IGNORECASE)),
    ("«X became Y» in serie",
     re.compile(r"(?:\b\w+\s+became\s+\w+\.\s+){2,}|(?:\b\w+\s+divenne\s+\w+\.\s+){2,}",
                re.IGNORECASE)),
]

#: Le virgolette del dialogo: dritte, curve, caporali.
_DIALOGUE = re.compile(r"“[^”]{2,}”|\"[^\"\n]{2,}\"|«[^»]{2,}»")


def words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def count_words(text: str) -> int:
    """Le parole del testo, senza i titoli: è il testo che il lettore legge."""
    corpo = "\n".join(r for r in (text or "").splitlines() if not r.strip().startswith("#"))
    return len(_WORD.findall(corpo))


def prose_only(text: str) -> str:
    """Il testo senza la bibliografia in fondo: le note non sono prosa del capitolo."""
    parti = re.split(r"(?im)^##\s+(?:Note bibliografiche|Bibliograph\w*|References|Fonti)\s*$",
                     text or "", maxsplit=1)
    return parti[0]


def paragraphs(text: str) -> list[str]:
    """I paragrafi di prosa, nell'ordine, senza titoli e righe vuote."""
    return [b.strip() for b in re.split(r"\n\s*\n", prose_only(text))
            if b.strip() and not b.lstrip().startswith("#")]


def scenes(text: str) -> list[tuple[str, list[str]]]:
    """Le sezioni ``##`` del capitolo: ``[(titolo, paragrafi)]``. Il testo prima della prima
    sezione, se c'è, sta sotto un titolo vuoto."""
    fuori: list[tuple[str, list[str]]] = []
    titolo, blocchi = "", []
    for blocco in re.split(r"\n\s*\n", prose_only(text)):
        b = blocco.strip()
        if not b:
            continue
        prima = b.splitlines()[0].strip()
        if prima.startswith("## "):
            if blocchi or titolo:
                fuori.append((titolo, blocchi))
            titolo, blocchi = prima[3:].strip(), []
            resto = "\n".join(b.splitlines()[1:]).strip()
            if resto:
                blocchi.append(resto)
        elif prima.startswith("#"):
            continue
        else:
            blocchi.append(b)
    if blocchi or titolo:
        fuori.append((titolo, blocchi))
    return fuori


def content_words(text: str) -> list[str]:
    return [w for w in (p.lower() for p in words(text)) if len(w) > 2 and w not in STOPWORDS]


def _stem(word: str) -> str:
    """Una radice grossolana, perché «available» e «availability» contino insieme."""
    w = word.lower()
    for coda in ("abilities", "ability", "ibility", "ations", "ation", "ments", "ment", "ness",
                 "ities", "ity", "ings", "ing", "able", "ible", "ies", "ed", "es", "s"):
        if w.endswith(coda) and len(w) - len(coda) >= 4:
            return w[: -len(coda)]
    return w


# --------------------------------------------------------------------------- #
#  paragrafi che dicono la stessa cosa
# --------------------------------------------------------------------------- #
def _vectors(blocchi: list[str]) -> list[dict[str, float]]:
    """TF-IDF sulle radici delle parole piene: due paragrafi che ripetono la tesi con sinonimi
    parziali condividono comunque le parole che la portano."""
    sacchi = [Counter(_stem(w) for w in content_words(b)) for b in blocchi]
    df: Counter[str] = Counter()
    for s in sacchi:
        df.update(s.keys())
    n = len(sacchi)
    vettori = []
    for s in sacchi:
        v = {t: c * (math.log((1 + n) / (1 + df[t])) + 1) for t, c in s.items()}
        norma = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vettori.append({t: x / norma for t, x in v.items()})
    return vettori


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


@dataclass
class SimilarPair:
    first: int
    second: int
    similarity: float


def similar_paragraphs(blocchi: list[str], threshold: float = 0.45,
                       min_words: int = 25) -> list[SimilarPair]:
    """Le coppie di paragrafi che si somigliano oltre la soglia (coseno TF-IDF)."""
    vettori = _vectors(blocchi)
    lunghi = [i for i, b in enumerate(blocchi) if len(words(b)) >= min_words]
    coppie = []
    for x, i in enumerate(lunghi):
        for j in lunghi[x + 1:]:
            s = _cos(vettori[i], vettori[j])
            if s >= threshold:
                coppie.append(SimilarPair(i, j, round(s, 2)))
    return coppie


def shingles(text: str, k: int = 5) -> set[str]:
    ws = [w.lower() for w in words(text)]
    return {" ".join(ws[i:i + k]) for i in range(max(0, len(ws) - k + 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------- #
#  parole che si addensano
# --------------------------------------------------------------------------- #
@dataclass
class Burst:
    term: str
    count: int
    per_thousand: float
    baseline: float  #: la densità media nel resto del libro (0 se non c'è un resto)
    paragraphs: list[int] = field(default_factory=list)  #: dove si concentra


def term_densities(text: str) -> dict[str, float]:
    """Occorrenze per mille parole di ogni radice piena."""
    cw = [_stem(w) for w in content_words(prose_only(text))]
    totale = max(1, count_words(prose_only(text)))
    return {t: c * 1000 / totale for t, c in Counter(cw).items()}


def term_bursts(text: str, baseline: dict[str, float] | None = None, *, min_count: int = 15,
                factor: float = 3.0, floor: float = 4.0, limit: int = 12) -> list[Burst]:
    """Le parole che nel capitolo tornano molto più del solito.

    ``baseline`` è la densità media negli altri capitoli del libro (vedi :func:`book_baseline`):
    «pattern» può essere la parola del libro, e allora non è un'anomalia del capitolo. Senza
    una base si guarda solo la densità assoluta (``floor`` occorrenze ogni mille parole).
    """
    blocchi = paragraphs(text)
    cw_per_blocco = [Counter(_stem(w) for w in content_words(b)) for b in blocchi]
    totale = max(1, count_words(prose_only(text)))
    conti: Counter[str] = Counter()
    for c in cw_per_blocco:
        conti.update(c)
    fuori = []
    for term, n in conti.most_common():
        if n < min_count:
            break
        densita = n * 1000 / totale
        base = (baseline or {}).get(term, 0.0)
        soglia = max(floor, base * factor) if baseline else floor
        if densita < soglia:
            continue
        dove = sorted(range(len(blocchi)), key=lambda i: -cw_per_blocco[i][term])
        dove = sorted(i for i in dove[:6] if cw_per_blocco[i][term] >= 2)
        fuori.append(Burst(_display(term, blocchi), n, round(densita, 1), round(base, 1), dove))
        if len(fuori) >= limit:
            break
    return fuori


def _display(stem: str, blocchi: list[str]) -> str:
    """La forma più frequente di una radice, per mostrarla a chi legge."""
    forme = Counter(w.lower() for b in blocchi for w in words(b) if _stem(w) == stem)
    return forme.most_common(1)[0][0] if forme else stem


def book_baseline(other_chapters: list[str]) -> dict[str, float]:
    """La densità media di ogni radice negli altri capitoli: la misura del libro."""
    if not other_chapters:
        return {}
    somma: Counter[str] = Counter()
    for testo in other_chapters:
        somma.update(term_densities(testo))
    return {t: v / len(other_chapters) for t, v in somma.items()}


# --------------------------------------------------------------------------- #
#  formule in serie, dialogo, protagonista
# --------------------------------------------------------------------------- #
def formula_hits(text: str) -> dict[str, list[int]]:
    """Per ogni formula retorica, i paragrafi in cui compare (con ripetizioni)."""
    fuori: dict[str, list[int]] = {}
    for i, b in enumerate(paragraphs(text)):
        for nome, schema in FORMULAS:
            n = len(schema.findall(b))
            if n:
                fuori.setdefault(nome, []).extend([i] * n)
    return fuori


def dialogue_share(text: str) -> float:
    """La parte del testo detta dai personaggi, fra 0 e 1."""
    corpo = prose_only(text)
    totale = count_words(corpo)
    if not totale:
        return 0.0
    detto = sum(len(words(m)) for m in _DIALOGUE.findall(corpo))
    return round(detto / totale, 3)


def name_tokens(name: str) -> list[str]:
    """Le parti di un nome con cui il testo può chiamare un personaggio («Nicolò», «Sforza»)."""
    return [p for p in words(name) if len(p) > 2 and p[0].isupper()]


def mentions(text: str, name: str) -> int:
    parti = name_tokens(name)
    if not parti:
        return 0
    schema = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in parti) + r")\b")
    return len(schema.findall(prose_only(text)))


# --------------------------------------------------------------------------- #
#  il resoconto
# --------------------------------------------------------------------------- #
@dataclass
class ChapterMetrics:
    words: int
    paragraphs: int
    scenes: int
    similar: list[SimilarPair]
    bursts: list[Burst]
    formulas: dict[str, list[int]]
    dialogue: float
    protagonist: str = ""
    protagonist_mentions: int = 0
    #: per ogni sezione ``##``: (titolo, parole, menzioni del protagonista)
    per_scene: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def serial_formulas(self) -> dict[str, list[int]]:
        """Le formule che tornano almeno tre volte: sotto, è stile; sopra, è un tic."""
        return {k: v for k, v in self.formulas.items() if len(v) >= 3}

    def flagged_paragraphs(self) -> set[int]:
        """I paragrafi che l'editor deve guardare: somiglianze, addensamenti, formule in serie."""
        dove: set[int] = set()
        for c in self.similar:
            dove.update((c.first, c.second))
        for b in self.bursts:
            dove.update(b.paragraphs)
        for posti in self.serial_formulas.values():
            dove.update(posti)
        return dove

    def markdown(self) -> str:
        righe = [f"- Parole: {self.words} · paragrafi: {self.paragraphs} · sezioni: {self.scenes}",
                 f"- Dialogo: {round(self.dialogue * 100)}% del testo"]
        if self.protagonist:
            righe.append(f"- {self.protagonist}: nominato {self.protagonist_mentions} volte")
        if self.per_scene:
            righe.append("- Per sezione: " + "; ".join(
                f"«{t or '—'}» {p} parole, protagonista ×{m}" for t, p, m in self.per_scene))
        if self.similar:
            righe.append(f"- Paragrafi che si somigliano: {len(self.similar)} coppie — " + ", ".join(
                f"§{c.first + 1}~§{c.second + 1} ({c.similarity})" for c in self.similar[:12]))
        if self.bursts:
            righe.append("- Parole addensate (per mille parole, fra parentesi la media del "
                         "libro): " + ", ".join(
                             f"{b.term} ×{b.count} ({b.per_thousand}"
                             + (f" / {b.baseline}" if b.baseline else "") + ")"
                             for b in self.bursts))
        if self.serial_formulas:
            righe.append("- Formule in serie: " + ", ".join(
                f"{k} ×{len(v)}" for k, v in self.serial_formulas.items()))
        return "\n".join(righe)


def measure(text: str, *, protagonist: str = "", baseline: dict[str, float] | None = None
            ) -> ChapterMetrics:
    blocchi = paragraphs(text)
    sezioni = scenes(text)
    return ChapterMetrics(
        words=count_words(prose_only(text)),
        paragraphs=len(blocchi),
        scenes=len([s for s in sezioni if s[0]]),
        similar=similar_paragraphs(blocchi),
        bursts=term_bursts(text, baseline),
        formulas=formula_hits(text),
        dialogue=dialogue_share(text),
        protagonist=protagonist,
        protagonist_mentions=mentions(text, protagonist) if protagonist else 0,
        per_scene=[(t, count_words("\n\n".join(p)),
                    mentions("\n\n".join(p), protagonist) if protagonist else 0)
                   for t, p in sezioni],
    )


def length_drift(before: int, after: int) -> float:
    """La variazione di lunghezza, con segno: -0,12 vuol dire un testo più corto del 12%."""
    return (after - before) / before if before else 0.0
