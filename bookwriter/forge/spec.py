"""Fase 2 — la specifica: ``MASTER_SPEC.md``, e l'approvazione dell'autore.

È il documento contro cui lavorano tutte le fasi successive. Non è un riassunto del libro: è
l'insieme dei vincoli che fanno di questo libro **questo** libro e non un altro. Quando il libro
e la specifica dicono cose diverse, una delle due è sbagliata, e bisogna decidere quale.

Quasi tutto qui è trascrizione: il quaderno del libro, nato dalla conversazione con l'autore,
finisce nei suoi posti senza che un modello ci metta mano, perché riscriverlo vorrebbe dire
perderne un pezzo. Al modello si
chiede l'unica cosa che è davvero un lavoro — la **struttura**: quanti capitoli, in che ordine,
cosa fa ciascuno, quante parole ha a disposizione.

E la struttura si mostra all'autore prima di scrivere una riga. Rivedere un indice costa minuti;
rivedere venti capitoli già scritti costa giorni.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from bookwriter.forge import notebook as nb
from bookwriter.forge.project import BookProject, ForgeState

OUTLINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "number": {"type": "integer"},
                    "title": {"type": "string"},
                },
                "required": ["number", "title"],
            },
        },
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "number": {"type": "integer"},
                    "part": {"type": "integer"},
                    "title": {"type": "string"},
                    "does": {"type": "string"},
                    "points": {"type": "array", "items": {"type": "string"}},
                    "words": {"type": "integer"},
                },
                "required": ["number", "part", "title", "does", "points", "words"],
            },
        },
        "front_matter": {"type": "array", "items": {"type": "string"}},
        "back_matter": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["parts", "chapters", "front_matter", "back_matter", "rationale"],
}


class SupportsChat(Protocol):
    def chat(self, messages: list[dict[str, str]], *, model: str | None = ...,
             extra: dict[str, Any] | None = ...) -> str:
        ...

    def chat_json(self, messages: list[dict[str, str]], *, schema: dict,
                  model: str | None = ...) -> Any:
        ...


_NUMBER = re.compile(r"(\d[\d.\s']*\d|\d)")

#: Un numero con i suoi separatori delle migliaia e il suo eventuale «k» o «mila».
_AMOUNT = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,' ]\d{3})+|\d+(?:[.,]\d+)?)\s*(k\b|mila\b|migliaia\b|thousand\b)?",
    re.IGNORECASE)
_WORDS_AFTER = re.compile(r"^\s*(?:parole|parola|words?)\b", re.IGNORECASE)
_TOTAL = re.compile(r"total[ei]?|in tutto|complessiv|overall|in total|\bsum\b", re.IGNORECASE)
_EACH = re.compile(r"ciascun|per capitolo|a capitolo|ogni capitolo|\beach\b|per chapter|"
                   r"every chapter|per ogni", re.IGNORECASE)
_CHAPTER_COUNT = re.compile(r"(\d{1,3})\s*(?:capitoli|capitolo|chapters?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class WordBudget:
    """Cosa dice il quaderno sulla lunghezza: il totale, o la misura di un capitolo."""

    total: int | None = None
    per_chapter: int | None = None
    chapters: int | None = None  #: i capitoli nominati nel testo stesso («15 capitoli»)


def _amount(token: str, suffix: str | None) -> int:
    """«105.000», «105,000», «105 000», «7'000», «105k», «105 mila», «7,5 mila» → numero."""
    t = token.replace("'", "").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", t):
        valore = float(re.sub(r"[.,]", "", t))
    elif re.fullmatch(r"\d+[.,]\d+", t):
        valore = float(t.replace(",", "."))
    else:
        valore = float(t)
    if suffix:
        valore *= 1000
    return int(round(valore))


def parse_budget(text: str, chapters: int | None = None) -> WordBudget:
    """La lunghezza dichiarata, distinguendo il **totale** dalla misura **per capitolo**.

    «15 capitoli base, ~7.000 parole ciascuno (~105.000 parole totali)» dice due volte la stessa
    cosa, e prendere il primo numero vicino a «parole» come totale (7.000) dava un libro da 7.000
    parole in tutto, cioè capitoli da cinquecento. Qui una dichiarazione esplicita del totale
    vince; altrimenti la misura per capitolo si moltiplica per i capitoli — quelli nominati nel
    testo, o quelli chiesti, o, in mancanza, la misura resta per capitolo e il totale ignoto.
    """
    testo = (text or "").lower()
    totale = per_capitolo = None
    for m in _AMOUNT.finditer(testo):
        dopo = testo[m.end():]
        if not _WORDS_AFTER.match(dopo):
            continue
        # Il contesto è stretto di proposito: i marcatori vicini al numero, non quelli di un
        # numero accanto. Dopo si taglia al numero o alla parentesi che segue.
        vicino_dopo = re.split(r"[\d(,;]", dopo[:30])[0]
        vicino_prima = re.split(r"[,;(]", testo[max(0, m.start() - 22):m.start()])[-1]
        contesto = vicino_prima + " " + vicino_dopo
        valore = _amount(m.group(1), m.group(2))
        if _TOTAL.search(contesto) and totale is None:
            totale = valore
        elif _EACH.search(contesto) and per_capitolo is None:
            per_capitolo = valore
    nominati = _CHAPTER_COUNT.search(testo)
    n_testo = int(nominati.group(1)) if nominati else None
    if totale is None and per_capitolo is not None:
        n = chapters or n_testo
        if n:
            totale = per_capitolo * n
    return WordBudget(total=totale, per_chapter=per_capitolo, chapters=n_testo)


def parse_words(text: str, chapters: int | None = None) -> int | None:
    """Il numero di parole da una risposta discorsiva: «55.000 parole ≈ 190 pagine» → 55000.

    Prima si guarda se la risposta distingue totale e misura per capitolo (:func:`parse_budget`);
    se no, si prende il numero che sta vicino alla parola «parole»; se non c'è, il più grande,
    perché fra parole e pagine il numero grande è sempre quello delle parole.
    """
    dichiarato = parse_budget(text, chapters)
    if dichiarato.total:
        return dichiarato.total
    if dichiarato.per_chapter:
        return None  # la misura di un capitolo non è il totale: senza capitoli il totale è ignoto
    text = (text or "").lower()
    numeri = [int(re.sub(r"[.\s']", "", m.group(1))) for m in _NUMBER.finditer(text)]
    numeri = [n for n in numeri if n >= 10]
    if not numeri:
        return None
    vicino = re.search(r"(\d[\d.\s']*)\s*(?:mila\s*)?parole|words", text)
    if vicino:
        try:
            return int(re.sub(r"[.\s']", "", vicino.group(1)))
        except (TypeError, ValueError):
            pass
    return max(numeri)


def target_words(state: ForgeState, *, fallback: int = 55_000, chapters: int | None = None) -> int:
    return parse_words(nb.get(state, "length"), chapters) or fallback


def declared_per_chapter(state: ForgeState, chapters: int | None = None) -> int | None:
    """La misura di un capitolo, se il quaderno la dichiara («~7.000 parole ciascuno»)."""
    dichiarato = parse_budget(nb.get(state, "length"), chapters)
    if dichiarato.per_chapter:
        return dichiarato.per_chapter
    return None


def fmt_words(n: int) -> str:
    """105000 → «105.000»: come si scrive in italiano."""
    return f"{int(n):,}".replace(",", ".")


# --------------------------------------------------------------------------- #
#  la struttura
# --------------------------------------------------------------------------- #
def propose_outline(
    state: ForgeState,
    client: SupportsChat,
    *,
    model: str | None = None,
    research: str = "",
    chapters: int | None = None,
) -> dict[str, Any]:
    """Chiede al modello la struttura del libro: parti, capitoli, cosa fa ciascuno, quante parole.

    ``research`` è il panorama della fase 3, quando c'è: sapere cosa coprono i libri esistenti e
    dove sbagliano cambia la struttura più di qualunque altra cosa, e per questo la ricerca viene
    prima dell'indice e non dopo.
    """
    parole = target_words(state, chapters=chapters)
    per_capitolo = declared_per_chapter(state, chapters)
    totale_noto = parse_words(nb.get(state, "length"), chapters)
    brief = _brief(state)
    misura = (f"L'autore dichiara circa {fmt_words(per_capitolo)} parole PER CAPITOLO: ogni "
              "capitolo sta attorno a quella misura, non molto sotto. " if per_capitolo else "")
    quanti = (f"Fai esattamente {chapters} capitoli." if chapters else
              "Decidi tu quanti capitoli servono: di norma fra 8 e 30, secondo la forma e la "
              "lunghezza. Le parti servono solo se i capitoli superano gli otto o se il libro ha "
              "movimenti distinti.")
    sistema = (
        "Sei un editor che progetta la struttura di un libro. Dai una struttura in cui ogni "
        "capitolo fa UNA cosa sola e la dice in una riga: non un titolo generico, ma quello che "
        "il capitolo stabilisce e che i successivi possono dare per acquisito. "
        "Di ogni capitolo dai anche da 3 a 6 PUNTI CHIAVE: le scene, i passaggi o gli argomenti "
        "che il capitolo deve portare, nell'ordine in cui compaiono, ciascuno in una riga "
        "concreta (non «introduzione» ma quello che l'introduzione stabilisce). Sono la base su "
        "cui si cercano le fonti e si scrive: ogni punto deve poter essere documentato o "
        "scritto da solo. "
        f"{quanti} "
        f"{misura}"
        + (f"Il budget totale è di {parole} parole: distribuiscilo fra i capitoli in modo che la "
           "somma ci arrivi vicino, dando più spazio dove c'è più da dimostrare e meno alle "
           "aperture e alle chiusure. " if totale_noto or not per_capitolo else
           "Il totale dei capitoli lo fa la misura per capitolo: il numero dei capitoli lo "
           "decidi tu. ")
        + "In 'rationale' spiega in tre righe perché la struttura è "
        "questa e cosa hai deliberatamente lasciato fuori. "
        "Il libro è descritto dal suo quaderno: la catena degli avvenimenti, le scene chiave e il "
        "finale vanno distribuiti fra i capitoli in modo che i nessi causali reggano e che ogni "
        "scena chiave abbia un capitolo che la porta; la lunghezza dei capitoli segue il lettore. "
        f"Scrivi titoli e descrizioni in {nb.language_name(state)}."
    )
    utente = brief + (f"\n\n## Panorama della ricerca\n{research}" if research else "")
    risultato = client.chat_json(
        [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
        schema=OUTLINE_SCHEMA, model=model,
    )
    return _normalise_outline(risultato, parole, per_capitolo)


def _normalise_outline(risultato: dict[str, Any], parole: int,
                       per_capitolo: int | None = None) -> dict[str, Any]:
    """Rinumera in ordine di lettura e ripara i budget: un modello sbaglia a sommare.

    Se l'autore ha dichiarato la misura di un capitolo, quella è un vincolo: un capitolo che se ne
    discosta di oltre il 30% ci torna.
    """
    capitoli = [c for c in (risultato.get("chapters") or []) if isinstance(c, dict)]
    capitoli.sort(key=lambda c: (int(c.get("part") or 1), int(c.get("number") or 0)))
    for i, c in enumerate(capitoli, 1):
        c["number"] = i
        c["part"] = int(c.get("part") or 1)
        c["title"] = str(c.get("title") or f"Capitolo {i}").strip()
        c["does"] = str(c.get("does") or "").strip()
        c["points"] = normalise_points(c.get("points"))
        c["words"] = max(300, int(c.get("words") or 0))
    if per_capitolo:
        for c in capitoli:
            if abs(c["words"] - per_capitolo) > per_capitolo * 0.3:
                c["words"] = per_capitolo
    somma = sum(c["words"] for c in capitoli)
    if capitoli and somma and not per_capitolo and abs(somma - parole) > parole * 0.05:
        fattore = parole / somma
        for c in capitoli:
            c["words"] = int(round(c["words"] * fattore / 100.0) * 100)
    risultato["chapters"] = capitoli
    risultato["parts"] = [
        {"number": int(p.get("number") or i), "title": str(p.get("title") or "").strip()}
        for i, p in enumerate(risultato.get("parts") or [], 1) if isinstance(p, dict)
    ]
    return risultato


#: Quanti punti chiave al massimo ha un capitolo: oltre, erano due capitoli.
MAX_POINTS = 6


def normalise_points(raw: Any) -> list[str]:
    """I punti chiave di un capitolo: righe non vuote, senza doppioni, al più ``MAX_POINTS``."""
    if not isinstance(raw, (list, tuple)):
        return []
    visti: list[str] = []
    for voce in raw:
        testo = " ".join(str(voce or "").split()).strip(" -•*")
        if testo and testo not in visti:
            visti.append(testo)
    return visti[:MAX_POINTS]


def chapter_points(chapter: dict[str, Any]) -> list[str]:
    """I punti su cui si cerca e si scrive. Un libro senza punti (una specifica vecchia) ne ha
    uno solo per capitolo: il capitolo intero."""
    punti = normalise_points(chapter.get("points"))
    if punti:
        return punti
    intero = str(chapter.get("does") or chapter.get("title") or "").strip()
    return [intero] if intero else []


_POINTS_HEADING = re.compile(r"^#{3,4}\s+Capitolo\s+(\d{1,3})\b")
POINTS_SECTION = "Punti chiave dei capitoli"


def parse_points(text: str) -> dict[int, list[str]]:
    """I punti chiave scritti nella specifica: numero di capitolo → punti.

    Sono righe ``- punto`` sotto un titolo ``#### Capitolo N``; l'autore li modifica a mano, e
    ``save_spec`` li rilegge. La tabella dei capitoli resta la fonte dei capitoli: qui si leggono
    solo i punti.
    """
    trovati: dict[int, list[str]] = {}
    corrente: int | None = None
    for riga in text.splitlines():
        if match := _POINTS_HEADING.match(riga.strip()):
            corrente = int(match.group(1))
            trovati.setdefault(corrente, [])
        elif riga.strip().startswith("#") or riga.strip().startswith("---"):
            corrente = None
        elif corrente is not None and (voce := re.match(r"^\s*[-*•]\s+(.+)$", riga)):
            trovati[corrente].append(voce.group(1).strip())
    return {n: normalise_points(p) for n, p in trovati.items()}


def has_points_section(text: str) -> bool:
    return POINTS_SECTION in text


def _brief(state: ForgeState) -> str:
    """Il quaderno del libro in forma di brief, per i prompt che devono conoscere il libro."""
    return nb.brief(state)


# --------------------------------------------------------------------------- #
#  il documento
# --------------------------------------------------------------------------- #
def render_spec(
    state: ForgeState,
    outline: dict[str, Any] | None = None,
    *,
    novelty: str = "",
    competitors: list[dict[str, str]] | None = None,
    style: str = "",
) -> str:
    """``MASTER_SPEC.md`` dal quaderno del libro e dalla struttura.

    Di stampa qui non c'è niente: misura, copertina e canale stanno in ``book.yaml``, che si
    scrive a mano nella fase di produzione. La specifica dice che libro è, non che oggetto.
    """
    outline = outline or {}
    capitoli = outline.get("chapters") or []
    parti = {int(p["number"]): p.get("title", "") for p in (outline.get("parts") or [])}
    parole = target_words(state, chapters=len(capitoli) or None)
    quaderno = nb.normalise(state.notebook)

    def sezione(numero: int, titolo: str, chiavi: tuple[str, ...]) -> list[str]:
        corpo = nb.render({k: quaderno[k] for k in chiavi}, level=3)
        return ["---", "", f"## {numero}. {titolo}", "", corpo or "*(da definire)*", ""]

    righe: list[str] = [
        f"# SPECIFICA — {nb.get(state, 'title') or 'Senza titolo'}",
        "",
        "> Il documento contro cui lavora ogni fase successiva. Scritto dopo la conversazione",
        "> con l'autore, approvato da lui prima che venga steso un solo capitolo. Quando il libro",
        "> e questo file dicono cose diverse, una delle due è sbagliata: decidi quale e correggila.",
        "",
        (f"**Lingua del libro:** {nb.language_name(state)} · "
         f"**Stato:** {'approvata' if state.spec_approved else 'bozza'} · "
         f"**Ultima revisione:** {date.today().isoformat()}"),
        "",
        # Il budget si vede in testa: un libro da 105.000 parole finito a 7.000 si è già visto.
        f"**Obiettivo:** {fmt_words(parole)} parole"
        + (f" — circa {fmt_words(parole // len(capitoli))} per capitolo" if capitoli else ""),
        "",
    ]
    righe += sezione(1, "Il libro", ("form", "premise", "author_goal", "messages",
                                     "reader_effect"))
    if competitors or novelty:
        righe += ["**Perché questo libro e non quelli che esistono:**", ""]
        if competitors:
            righe += ["| Titolo esistente | Cosa fa | Cosa gli manca |", "|---|---|---|"]
            righe += [f"| {c.get('title', '')} | {c.get('does', '')} | {c.get('misses', '')} |"
                      for c in competitors]
            righe.append("")
        if novelty:
            righe += [f"**Dove è davvero nuovo:** {novelty}", ""]
    righe += sezione(2, "Il lettore", ("audience", "adaptation", "length"))
    righe += sezione(3, "La trama", ("plot", "events", "causality", "psychology"))
    righe += sezione(4, "Scene chiave e finale", ("key_scenes", "ending"))
    righe += sezione(5, "Personaggi", ("characters",))
    righe += sezione(6, "Il mondo", ("setting", "places"))

    righe += [
        "---", "", "## 7. Struttura", "",
        "Il budget di parole per capitolo, e una riga che dice cosa fa ciascuno. Questa tabella",
        "si rivede **prima** di scrivere, non dopo.", "",
    ]
    if capitoli:
        for numero in sorted({c["part"] for c in capitoli}):
            titolo_parte = parti.get(numero, "")
            if len(parti) > 1 or titolo_parte:
                righe += [f"### Parte {numero} — {titolo_parte}".rstrip(" —"), ""]
            righe += ["| # | Capitolo | Cosa fa | Parole |", "|---|---|---|---|"]
            righe += [
                f"| {c['number']} | {c['title']} | {c['does']} | {c['words']} |"
                for c in capitoli if c["part"] == numero
            ]
            righe.append("")
        righe.append(f"**Totale previsto:** {fmt_words(sum(c['words'] for c in capitoli))} parole "
                     f"(obiettivo {fmt_words(parole)})")
        if any(c.get("points") for c in capitoli):
            righe += ["", f"### {POINTS_SECTION}", "",
                      "Le scene, i passaggi o gli argomenti che ogni capitolo porta. Si cercano le",
                      "fonti e si scrive punto per punto: un punto per riga, modificabili a mano.", ""]
            for c in capitoli:
                if c.get("points"):
                    righe += [f"#### Capitolo {c['number']} — {c['title']}", ""]
                    righe += [f"- {p}" for p in c["points"]]
                    righe.append("")
    else:
        righe.append("*(struttura non ancora proposta)*")
    righe += [
        "",
        f"**Pagine iniziali:** {', '.join(outline.get('front_matter') or []) or '—'}",
        f"**Pagine finali:** {', '.join(outline.get('back_matter') or []) or '—'}",
        "",
    ]
    if rationale := str(outline.get("rationale") or "").strip():
        righe += [f"**Perché questa struttura:** {rationale}", ""]

    righe += ["---", "", "## 8. Voce", "",
              "Lo stile è quello scelto nell'interfaccia" + (":" if style.strip() else
                                                             ": prosa chiara."), ""]
    if style.strip():
        righe += ["```", style.strip(), "```", ""]
    if quaderno["style_notes"]:
        righe += [f"**Precisazioni:** {quaderno['style_notes']}", ""]

    righe += [
        "---", "", "## 9. Prove e ricerca", "",
        "**Regola:** nessun numero, nessuna affermazione di fatto e nessuna citazione entra nel",
        "manoscritto senza che la fonte sia già scritta in `research/`.", "",
    ]
    righe += sezione(10, "Vincoli", ("avoid",))
    righe += sezione(11, "Decisioni", ("agreed", "assistant_decisions"))
    righe += [
        "---", "", "## 12. Accordo di lavoro", "",
        "- **Dove vivono le bozze:** `chapters/NN_nome.md`",
        "- **Quando un capitolo è finito:** scritto, documentato, dentro il budget, riletto una volta.",
        ("- **Quando è finito il libro:** tutti i capitoli fatti, controlli di stampa puliti, "
         "quel che resta aperto elencato in `output/PRODUCTION_NOTES.md`."),
        "",
    ]
    aperte = list(quaderno["open_questions"]) + list(state.open_items)
    if aperte:
        righe += ["---", "", "## 13. Questioni aperte", ""]
        righe += [f"- {voce}" for voce in aperte]
        righe.append("")
    if vecchie := nb.legacy_answers(state):
        righe += ["---", "", "## Dall'intervista precedente", "", vecchie, ""]
    return "\n".join(righe)


def write_spec(project: BookProject, state: ForgeState, outline: dict[str, Any] | None = None,
               **kwargs) -> tuple[str, list[dict[str, Any]]]:
    """Scrive ``MASTER_SPEC.md`` su disco. Prima si scrive, poi si racconta."""
    project.scaffold()
    testo = render_spec(state, outline, **kwargs)
    project.spec_path.write_text(testo, encoding="utf-8")
    if outline and outline.get("chapters"):
        state.outline = list(outline["chapters"])
    return testo, state.outline
