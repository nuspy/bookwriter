"""Motore di riscrittura deterministico (§8.5).

Legge i chunk **in ordine assoluto** (``book_pos``) e riscrive ciascuno mantenendo il contenuto ma
adottando uno stile (contratto). L'uniformità è garantita dal **ContinuityState** che scorre di
chunk in chunk: glossario coerente (nomi/termini), riassunto progressivo, coda di contesto. Lo stato
è persistito sulla run → ripartenza deterministica. Serve sia a Rewrite (B) sia a Translate (C).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.db.project_schema import (
    Chunk,
    Document,
    RewriteChunk,
    RewriteRun,
    make_engine,
)
from bookwriter.llm.client import LLMDegenerate, LLMError
from bookwriter.text_utils import has_prose, split_sentences

REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rewritten": {"type": "string"},
        "glossary": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"term": {"type": "string"}, "rendering": {"type": "string"}},
                "required": ["term", "rendering"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["rewritten", "glossary", "summary"],
}

_CRITIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "conforms": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["conforms", "issues"],
}


class SupportsChatJson(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


@dataclass
class RewriteReport:
    run_id: int
    processed: int
    total: int
    status: str
    copied: int = 0  # passaggi riportati invariati perché non c'era prosa da elaborare
    unchecked: int = 0  # passaggi consegnati senza verdetto: il giudice di stile non ha risposto
    stalled: int = 0  # passaggi su cui il modello è entrato in ripetizione
    salvaged: int = 0  # passaggi recuperati riprovandoli divisi in due
    stopped: bool = False  # interrotto su richiesta: il lavoro fatto è salvato e riprende da lì

    def summary(self) -> str:
        base = f"run #{self.run_id}: elaborati {self.processed}/{self.total} chunk ({self.status})"
        if self.stopped:
            base += " · interrotto: quanto fatto è salvato, riprende da qui"
        if self.copied:
            base += f" · {self.copied} riportati invariati (nessun testo da elaborare)"
        if self.salvaged:
            base += f" · {self.salvaged} recuperati dividendoli in due"
        if self.stalled:
            base += (
                f" · {self.stalled} RIMASTI NELLA LINGUA DI PARTENZA"
                " (il modello si è bloccato anche dividendoli)"
            )
        if self.unchecked:
            base += f" · {self.unchecked} senza controllo di stile (il giudice non ha risposto)"
        return base




def _continuity_text(continuity: dict) -> str:
    lines = []
    glossary = continuity.get("glossary") or {}
    if glossary:
        items = "; ".join(f"{t}→{r}" for t, r in list(glossary.items())[:40])
        lines.append(f"Rese coerenti da mantenere: {items}")
    if continuity.get("summary"):
        lines.append(f"Riassunto del già elaborato: {continuity['summary']}")
    if continuity.get("tail"):
        lines.append(f"Ultime frasi elaborate (continua senza ripeterle): {continuity['tail']}")
    return "\n".join(lines)


def _instruction(mode: str, target_language: str | None, ratio: float | None) -> str:
    if mode == "translate":
        base = f"Traduci il testo in {target_language}, mantenendo lo stile descritto e il contenuto."
    else:
        base = "Riscrivi il testo nello stile descritto, mantenendo fedele il CONTENUTO (nessuna informazione aggiunta o tolta)."
    base += " Usa il glossario per la coerenza di nomi e termini. Assicura continuità con le ultime frasi."
    if ratio:
        base += f" Punta a circa {round(ratio * 100)}% della lunghezza originale."
    base += (
        " CONSERVA L'IMPAGINAZIONE: restituisci lo stesso numero di paragrafi dell'originale, "
        "nello stesso ordine, separati da una riga vuota. Un paragrafo che comincia con dei "
        "cancelletti (## …) è un titolo: lascia i cancelletti dove sono ed elabora solo il titolo. "
        "Gli asterischi (*corsivo*, **grassetto**) sono formattazione del libro: vanno riportati "
        "sulle parole corrispondenti."
    )
    base += (
        " Rispondi in JSON: 'rewritten' (il testo), 'glossary' (nuovi nomi/termini con la resa scelta), "
        "'summary' (riassunto cumulativo aggiornato, incluso questo brano)."
    )
    return base


def _style_conforms(
    contract: str,
    text: str,
    target_language: str | None,
    client: SupportsChatJson,
    model: str | None,
    *,
    source: str = "",
) -> tuple[bool, list[str]]:
    """Verdetto sul testo prodotto: rispetta il contratto di stile?

    Al giudice serve l'ORIGINALE, non solo il risultato: quasi tutti i contratti chiedono
    fedeltà allo stile di partenza, e senza il termine di paragone il modello non può che
    inventare scostamenti — bocciando sistematicamente anche le rese buone. Gli si chiede
    inoltre di segnalare solo violazioni del contratto: senza questo vincolo elenca preferenze
    personali di lingua (una metafora, una frase breve) che poi la correzione appiattisce.
    """
    lang = f" Il testo deve essere scritto in {target_language}." if target_language else ""
    system = (
        "Giudichi se il TESTO rispetta il CONTRATTO di stile, confrontandolo con l'ORIGINALE "
        f"da cui è stato prodotto.{lang} Segnala SOLO violazioni concrete del contratto o "
        "travisamenti dell'originale, citando il passaggio. Non segnalare scelte espressive "
        "legittime (metafore, frasi brevi, registro dell'autore), varianti ortografiche "
        "ammesse, né la presunta assenza di istruzioni: se il contratto è generico, un testo "
        "fedele all'originale lo rispetta. In assenza di violazioni concrete rispondi "
        "conforms=true con 'issues' vuoto."
    )
    parts = [f"CONTRATTO:\n{contract}"]
    if source:
        parts.append(f"ORIGINALE:\n{source}")
    parts.append(f"TESTO:\n{text}")
    verdict = client.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}],
        schema=_CRITIC_SCHEMA,
        model=model,
    )
    issues = [str(i).strip() for i in (verdict.get("issues") or []) if str(i).strip()]
    # Un verdetto negativo senza addebiti non è un verdetto: non deve costare una riscrittura.
    return bool(verdict.get("conforms", True)) or not issues, issues


def _rewrite_one(
    chunk_text: str,
    contract: str,
    continuity: dict,
    client: SupportsChatJson,
    *,
    mode: str,
    target_language: str | None,
    ratio: float | None,
    model: str | None,
    critic: bool = True,
    critic_model: str | None = None,
) -> tuple[str, dict, bool]:
    system = contract + "\n" + _continuity_text(continuity) + "\n" + _instruction(mode, target_language, ratio)
    result = client.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": chunk_text}],
        schema=REWRITE_SCHEMA,
        model=model,
    )
    rewritten = str(result.get("rewritten", "")).strip()

    # Critic loop: una rigenerazione mirata se il testo non rispetta lo stile.
    checked = True
    if critic and rewritten:
        judge = critic_model or model
        try:
            conforms, issues = _style_conforms(
                contract, rewritten, target_language, client, judge, source=chunk_text
            )
            if not conforms and issues:
                fix = " Correggi questi scostamenti dallo stile: " + "; ".join(issues)
                candidate = client.chat_json(
                    [{"role": "system", "content": system + fix}, {"role": "user", "content": chunk_text}],
                    schema=REWRITE_SCHEMA,
                    model=model,
                )
                corrected = str(candidate.get("rewritten", "")).strip()
                # La correzione si adotta solo se supera la verifica che aveva bocciato la prima
                # stesura. Prenderla per buona alla cieca è peggio che non controllare: consegna
                # un testo rifatto su rilievi che nessuno ha confermato.
                if corrected:
                    improved, _ = _style_conforms(
                        contract, corrected, target_language, client, judge, source=chunk_text
                    )
                    if improved:
                        result, rewritten = candidate, corrected
        except LLMError:
            # Il controllo di stile è un di più: se il giudice non risponde (o si perde in un
            # ragionamento senza fine) si consegna la stesura già prodotta e lo si dichiara.
            # Lasciare che un accessorio faccia fallire il lavoro significava buttare ore di
            # traduzione per un verdetto mancante.
            checked = False

    # Aggiorna la continuità.
    glossary = dict(continuity.get("glossary") or {})
    for entry in result.get("glossary", []) or []:
        term, rendering = entry.get("term"), entry.get("rendering")
        if term and rendering and term not in glossary:  # prima resa vince → coerenza
            glossary[term] = rendering
    sentences = split_sentences(rewritten)
    tail = " ".join(sentences[-2:]) if sentences else rewritten[-300:]
    new_continuity = {
        "glossary": glossary,
        "summary": str(result.get("summary", continuity.get("summary", ""))).strip()[:1500],
        "tail": tail,
    }
    return rewritten, new_continuity, checked


def _spezza_a_meta(testo: str) -> tuple[str, str] | None:
    """Divide il brano in due su un confine di capoverso, il più vicino possibile alla metà."""
    capoversi = [p for p in testo.split("\n\n") if p.strip()]
    if len(capoversi) < 2:
        frasi = split_sentences(testo)
        if len(frasi) < 2:
            return None
        meta = len(frasi) // 2
        return " ".join(frasi[:meta]), " ".join(frasi[meta:])
    misure = [len(p) for p in capoversi]
    obiettivo, corrente, taglio = sum(misure) / 2, 0, 1
    for i, n in enumerate(misure[:-1], start=1):
        corrente += n
        if corrente >= obiettivo:
            taglio = i
            break
    return "\n\n".join(capoversi[:taglio]), "\n\n".join(capoversi[taglio:])


def _riprova_dividendo(
    chunk_text: str, contract: str, continuity: dict, client: SupportsChatJson, **opzioni
) -> tuple[str, dict, bool] | None:
    """Rifà un brano su cui il modello si è bloccato, dividendolo in due.

    A temperatura zero riprovare lo stesso identico testo dà lo stesso identico blocco: insistere
    è tempo regalato. Ma la ripetizione senza fine nasce quasi sempre da una **struttura** del
    brano — un elenco di domande, una tabella, una sequenza di voci tutte uguali — e le due metà
    prese separatamente non ce l'hanno più. Se anche così non passa, si conserva l'originale.
    """
    meta = _spezza_a_meta(chunk_text)
    if meta is None:
        return None
    pezzi: list[str] = []
    controllati = True
    for parte in meta:
        try:
            reso, continuity, checked = _rewrite_one(parte, contract, continuity, client, **opzioni)
        except LLMDegenerate:
            return None
        pezzi.append(reso)
        controllati = controllati and checked
    return "\n\n".join(p for p in pezzi if p.strip()), continuity, controllati


def _first_chapter(chunks: list[Chunk]) -> list[Chunk]:
    if not chunks:
        return []
    first_ref = chunks[0].chapter_ref
    if first_ref is None:
        return chunks[:2]
    out = []
    for c in chunks:
        if c.chapter_ref == first_ref:
            out.append(c)
        elif out:
            break
    return out


def rewrite_document(
    db_path: Path,
    document_id: int,
    contract: str,
    client: SupportsChatJson,
    *,
    style_profile_id: int | None = None,
    mode: str = "rewrite",
    target_language: str | None = None,
    ratio: float | None = None,
    kind: str = "full",
    model: str | None = None,
    critic: bool = True,
    critic_model: str | None = None,
    resume: bool = True,
    redo_stalled: bool = False,
    progress=None,
    should_stop=None,
) -> RewriteReport:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        chunks = list(
            ses.exec(select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.book_pos))
        )
    if not chunks:
        raise ValueError("Documento senza chunk: importalo prima.")

    targets = _first_chapter(chunks) if kind == "preview" else chunks

    # Recupera o crea la run.
    with Session(engine) as ses:
        run = None
        if resume and kind == "full":
            # La lingua e lo stile fanno parte dell'identità della lavorazione, non sono
            # dettagli: lo stesso libro tradotto in cinque lingue sono cinque lavori distinti.
            # Senza questi due vincoli riprenderebbero tutti lo stesso, ognuno saltando i brani
            # fatti dagli altri, e il risultato sarebbe un unico testo in cinque lingue mescolate.
            #
            # Con ``redo_stalled`` si riapre anche una lavorazione già chiusa: si sta correggendo
            # un libro consegnato, non riprendendone uno interrotto.
            stati = ("running", "done") if redo_stalled else ("running",)
            run = ses.exec(
                select(RewriteRun)
                .where(RewriteRun.document_id == document_id)
                .where(RewriteRun.kind == "full")
                .where(RewriteRun.mode == mode)
                .where(RewriteRun.target_language == target_language)
                .where(RewriteRun.style_profile_id == style_profile_id)
                .where(RewriteRun.status.in_(stati))
                .order_by(RewriteRun.id.desc())
            ).first()
        if run is None:
            run = RewriteRun(
                document_id=document_id,
                style_profile_id=style_profile_id,
                kind=kind,
                mode=mode,
                target_language=target_language,
                ratio=ratio,
                status="running",
                continuity={},
            )
            ses.add(run)
            ses.commit()
            ses.refresh(run)
        run_id = run.id
        continuity = dict(run.continuity or {})
        fatti = list(ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == run_id)).all())
        done_ids = {rc.chunk_id for rc in fatti}
        if redo_stalled:
            # I brani su cui il modello si era bloccato sono stati salvati **identici
            # all'originale**: nel libro tradotto sono un pezzo rimasto nella lingua di partenza.
            # Si tolgono da quelli fatti, così vengono ritentati — adesso anche dividendoli.
            originali = {
                c.id: c.text
                for c in ses.exec(select(Chunk).where(Chunk.document_id == document_id)).all()
            }
            da_rifare = [rc for rc in fatti if rc.text == originali.get(rc.chunk_id)]
            for rc in da_rifare:
                ses.delete(rc)
                done_ids.discard(rc.chunk_id)
            ses.commit()

    todo = [c for c in targets if c.id not in done_ids]
    copied = stalled = unchecked = salvati = 0
    fermato = 0  # quanti passaggi non sono stati nemmeno tentati, perché ci si è fermati
    # L'avanzamento si conta sul **libro intero**, non sui brani rimasti da fare: riprendendo
    # una traduzione a metà, «0 su 72» farebbe credere che il libro sia lungo 72 e che non sia
    # stato fatto niente, mentre di brani ne ha 97 e 25 erano già tradotti.
    gia_fatti = len(done_ids)
    if progress:
        progress(gia_fatti, len(targets))
    for idx, chunk in enumerate(todo):
        if should_stop and should_stop():
            # Interruzione richiesta. Ci si ferma **fra** due passaggi, mai dentro: ogni brano
            # già elaborato è sul disco, e la ripartenza riprende esattamente da qui.
            fermato = len(todo) - idx
            break
        if not has_prose(chunk.text):
            # Niente da elaborare (indice, punti di guida, numeri di pagina): si riporta il
            # passaggio com'è, senza spendere una chiamata e senza buchi nel testo finale.
            rewritten, copied = chunk.text, copied + 1
        else:
            try:
                rewritten, continuity, checked = _rewrite_one(
                    chunk.text, contract, continuity, client,
                    mode=mode, target_language=target_language, ratio=ratio, model=model,
                    critic=critic, critic_model=critic_model,
                )
                unchecked += 0 if checked else 1
            except LLMDegenerate:
                # Il modello si è bloccato a ripetere. Prima di rinunciare si riprova dividendo
                # il brano: il blocco nasce quasi sempre dalla sua struttura, e le due metà
                # separate di solito passano. Solo se fallisce anche così si conserva
                # l'originale — che nel libro tradotto resta un pezzo nella lingua di partenza,
                # ed è il difetto che questo tentativo serve a evitare.
                ripreso = _riprova_dividendo(
                    chunk.text, contract, continuity, client,
                    mode=mode, target_language=target_language, ratio=ratio, model=model,
                    critic=critic, critic_model=critic_model,
                )
                if ripreso is None:
                    rewritten, stalled = chunk.text, stalled + 1
                else:
                    rewritten, continuity, checked = ripreso
                    unchecked += 0 if checked else 1
                    salvati += 1
        with Session(engine) as ses:
            ses.add(
                RewriteChunk(
                    run_id=run_id,
                    chunk_id=chunk.id,
                    book_pos=chunk.book_pos,
                    chapter_ref=chunk.chapter_ref,
                    text=rewritten,
                )
            )
            db_run = ses.get(RewriteRun, run_id)
            db_run.continuity = continuity
            ses.add(db_run)
            ses.commit()
        if progress:
            progress(gia_fatti + idx + 1, len(targets))

    processed = len(done_ids) + len(todo) - fermato
    status = "done" if (kind == "full" and processed >= len(targets)) else "running"
    with Session(engine) as ses:
        db_run = ses.get(RewriteRun, run_id)
        db_run.status = status
        ses.add(db_run)
        ses.commit()
    if progress:
        progress(gia_fatti + len(todo) - fermato, len(targets))
    return RewriteReport(
        run_id=run_id, processed=processed, total=len(targets), status=status,
        copied=copied, stalled=stalled, unchecked=unchecked, salvaged=salvati,
        stopped=bool(fermato),
    )


def assemble_markdown(db_path: Path, run_id: int) -> str:
    """Ricompone il testo elaborato per ``book_pos``.

    Non re-inietta i titoli di capitolo: l'intestazione è già contenuta nel testo del primo chunk
    di ogni capitolo (ed è quindi resa nella lingua/stile di destinazione), evitando duplicati.
    """
    engine = make_engine(db_path)
    with Session(engine) as ses:
        run = ses.get(RewriteRun, run_id)
        doc = ses.get(Document, run.document_id) if run else None
        rows = list(
            ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == run_id).order_by(RewriteChunk.book_pos))
        )
    title = (doc.title if doc else None) or "Libro"
    lines: list[str] = [f"# {title}", ""]
    for rc in rows:
        if rc.text.strip():
            lines += [rc.text.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class LayoutAssembly:
    """Il libro elaborato rimesso nella sua impaginazione."""

    layout: Any  # DocumentLayout
    misaligned: int = 0  # blocchi in cui il modello non ha reso il numero di paragrafi atteso

    def note(self) -> str:
        if not self.misaligned:
            return ""
        return f" · {self.misaligned} blocchi con i paragrafi ricuciti (il modello li ha uniti o divisi)"


def _split_paragraphs(text: str, atteso: int) -> list[str]:
    """Paragrafi del testo elaborato: righe vuote, o singoli a capo se è così che li ha resi."""
    import re

    parti = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(parti) < atteso:
        per_riga = [p.strip() for p in text.split("\n") if p.strip()]
        if len(per_riga) == atteso:
            return per_riga
    return parti


def _align(segmenti: list[int], paragrafi: list[str]) -> tuple[dict[int, list[str]], bool]:
    """Assegna i paragrafi prodotti ai segmenti che il blocco copriva.

    Quando i conti tornano è un accoppiamento uno a uno. Quando non tornano non si scarta nulla:
    i paragrafi in più finiscono nell'ultimo segmento (che si sdoppierà), quelli in meno lasciano
    vuoti i segmenti rimasti, che spariscono. Il testo arriva sempre tutto; a rimetterci è al
    più uno stacco di riga, e il caso viene contato e dichiarato.
    """
    if not segmenti or not paragrafi:
        return {}, bool(segmenti) != bool(paragrafi)
    if len(paragrafi) == len(segmenti):
        return {i: [p] for i, p in zip(segmenti, paragrafi)}, False
    if len(paragrafi) > len(segmenti):
        fuori = {i: [p] for i, p in zip(segmenti[:-1], paragrafi)}
        fuori[segmenti[-1]] = paragrafi[len(segmenti) - 1 :]
        return fuori, True
    fuori = {i: [p] for i, p in zip(segmenti, paragrafi)}
    for i in segmenti[len(paragrafi) :]:
        fuori[i] = []
    return fuori, True


def assemble_layout(db_path: Path, run_id: int) -> LayoutAssembly | None:
    """Rimette il testo elaborato dentro l'impaginazione del documento di partenza.

    Il collegamento è geometrico, non a occhio: ogni blocco porta con sé l'intervallo di
    caratteri che occupava nel libro, e da quello si ricava esattamente su quali segmenti
    cadeva. Titoli, interruzioni di pagina, pagine bianche e tabelle non passano nemmeno da qui:
    restano dove l'autore li ha messi.

    ``None`` se il documento è stato importato prima che si conservasse la struttura: chi chiama
    ricade sulla resa in Markdown.
    """
    from collections import defaultdict

    from bookwriter.db.project_schema import get_document_layout

    engine = make_engine(db_path)
    with Session(engine) as ses:
        run = ses.get(RewriteRun, run_id)
        if run is None:
            return None
        layout = get_document_layout(db_path, run.document_id)
        if layout is None:
            return None
        rows = list(
            ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == run_id).order_by(RewriteChunk.book_pos))
        )
        originali = {c.id: c for c in ses.exec(select(Chunk).where(Chunk.document_id == run.document_id)).all()}

    # Per ogni segmento i pezzi ricevuti, con l'indicazione se aprono un capoverso nuovo: quelli
    # che arrivano da blocchi diversi sono la stessa frase spezzata dal chunker e vanno ricuciti;
    # quelli separati dal modello dentro un blocco sono capoversi veri e restano distinti.
    pezzi: dict[int, list[tuple[str, bool]]] = defaultdict(list)
    toccati: set[int] = set()
    disallineati = 0
    for rc in rows:
        chunk = originali.get(rc.chunk_id)
        if chunk is None:
            continue
        segmenti = layout.segments_in_span(chunk.span_start, chunk.span_end)
        toccati.update(segmenti)
        assegnati, storto = _align(segmenti, _split_paragraphs(rc.text, len(segmenti)))
        disallineati += 1 if storto else 0
        for idx, testi in assegnati.items():
            pezzi[idx].extend((testo, n > 0) for n, testo in enumerate(testi))

    nuovi = {i: _fondi(pezzi.get(i, [])) for i in toccati}
    return LayoutAssembly(layout=layout.replace_texts(nuovi), misaligned=disallineati)


def _fondi(pezzi: list[tuple[str, bool]]) -> list[str]:
    """Ricuce i pezzi che continuano lo stesso capoverso, tiene distinti quelli che ne aprono uno."""
    fuori: list[str] = []
    for testo, nuovo_capoverso in pezzi:
        if nuovo_capoverso and fuori:
            fuori.append(testo)
        elif fuori:
            fuori[-1] = f"{fuori[-1]} {testo}".strip()
        else:
            fuori.append(testo)
    return fuori


def write_result(db_path: Path, run_id: int, out_path: Path, fmt: str = "docx") -> tuple[Path, str]:
    """Scrive il libro elaborato nel formato richiesto. Ritorna il file e una nota per l'utente.

    Prima strada: rimettere il testo nell'impaginazione originale. Seconda, se quel documento è
    entrato nel progetto prima che la struttura si conservasse: la vecchia resa in Markdown, che
    il libro lo consegna comunque.
    """
    from bookwriter.exporter import write_output
    from bookwriter.layout import write_layout
    from bookwriter.tables import repair, repair_layout

    montato = assemble_layout(db_path, run_id)
    if montato is None:
        testo, _ = repair(assemble_markdown(db_path, run_id))
        write_output(testo, out_path, fmt)
        return out_path, "impaginazione non disponibile per questo documento: reimportalo per conservarla"
    # Le tabelle si rimettono in piedi qui, all'ultimo momento: il modello le restituisce come
    # le ha ricevute, e una separatrice storta le trasforma in righe di testo con delle barre.
    tabelle = repair_layout(montato.layout)
    write_layout(montato.layout, out_path, fmt)
    note = [montato.note().lstrip(" ·").strip()]
    if tabelle:
        note.append(tabelle.summary())
    return out_path, " · ".join(n for n in note if n)


def preview_pairs(db_path: Path, run_id: int) -> list[dict]:
    """Coppie originale↔riscritto per la preview del primo capitolo."""
    engine = make_engine(db_path)
    with Session(engine) as ses:
        rows = list(
            ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == run_id).order_by(RewriteChunk.book_pos))
        )
        pairs = []
        for rc in rows:
            original = ses.get(Chunk, rc.chunk_id)
            pairs.append(
                {
                    "chapter": rc.chapter_ref,
                    "original": original.text if original else "",
                    "rewritten": rc.text,
                }
            )
    return pairs
