"""Motore di riscrittura deterministico (§8.5).

Legge i chunk **in ordine assoluto** (``book_pos``) e riscrive ciascuno mantenendo il contenuto ma
adottando uno stile (contratto). L'uniformità è garantita dal **ContinuityState** che scorre di
chunk in chunk: glossario coerente (nomi/termini), riassunto progressivo, coda di contesto. Lo stato
è persistito sulla run → ripartenza deterministica. Serve sia a Rewrite (B) sia a Translate (C).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
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
from bookwriter.rewrite.literary import (
    DECISA,
    FORM_KEEP,
    FORM_PROSE_FROM_VERSE,
    FORMS,
    espansione,
    interpreta_scelta,
    istruzioni_di_forma,
    nota_per_il_giudice,
    registra_idiomi,
    rifinisci,
    testo_delle_scelte,
    trova_idiomi,
    unita_di_lavoro,
)
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
    polished: int = 0  # passaggi migliorati dalla rifinitura madrelingua
    polish_kept: int = 0  # passaggi in cui la rifinitura è stata scartata o non è riuscita
    idioms_asked: int = 0  # domande fatte all'utente su modi di dire e riferimenti culturali
    idioms_pending: int = 0  # voci ancora «non decise» nel registro della lavorazione
    style_source: str = ""  # da dove viene lo stile usato (estratto dall'originale, scelto…)
    form: str = FORM_KEEP  # forma del testo prodotto (keep | prose_from_verse)
    modernized: bool = False  # lingua modernizzata conservando la voce dell'autore

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
        if self.style_source:
            base += f" · stile: {self.style_source}"
        if self.form == FORM_PROSE_FROM_VERSE:
            base += " · dai versi alla prosa"
        if self.modernized:
            base += " · lingua modernizzata"
        if self.polished or self.polish_kept:
            base += f" · rifinitura madrelingua: {self.polished} brani rifiniti"
            if self.polish_kept:
                base += f", {self.polish_kept} lasciati com'erano"
        if self.idioms_asked:
            base += f" · {self.idioms_asked} modi di dire sottoposti alla tua scelta"
        if self.idioms_pending:
            base += f" · {self.idioms_pending} modi di dire in attesa di una tua decisione"
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


def _instruction(
    mode: str,
    target_language: str | None,
    ratio: float | None,
    *,
    form: str = FORM_KEEP,
    modernize: bool = False,
) -> str:
    if mode == "translate":
        base = f"Traduci il testo in {target_language}, mantenendo lo stile descritto e il contenuto."
    else:
        base = "Riscrivi il testo nello stile descritto, mantenendo fedele il CONTENUTO (nessuna informazione aggiunta o tolta)."
    base += " Usa il glossario per la coerenza di nomi e termini. Assicura continuità con le ultime frasi."
    if ratio:
        base += f" Punta a circa {round(ratio * 100)}% della lunghezza originale."
    if form == FORM_KEEP:
        base += (
            " CONSERVA L'IMPAGINAZIONE: restituisci lo stesso numero di paragrafi dell'originale, "
            "nello stesso ordine, separati da una riga vuota. Un paragrafo che comincia con dei "
            "cancelletti (## …) è un titolo: lascia i cancelletti dove sono ed elabora solo il titolo. "
            "Gli asterischi (*corsivo*, **grassetto**) sono formattazione del libro: vanno riportati "
            "sulle parole corrispondenti."
        )
    # Con la prosa dai versi il numero dei paragrafi smette di essere un vincolo: lo sostituisce
    # la conservazione dei soli titoli (capitoli, canti), che è quella che conta per il libro.
    trasformazioni = istruzioni_di_forma(mode, target_language, form=form, modernize=modernize)
    if trasformazioni:
        base += " " + trasformazioni
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
    form: str = FORM_KEEP,
    modernize: bool = False,
    vincoli: str = "",
) -> tuple[str, dict, bool]:
    system = (
        contract + "\n" + _continuity_text(continuity)
        + (f"\n{vincoli}" if vincoli else "")
        + "\n" + _instruction(mode, target_language, ratio, form=form, modernize=modernize)
    )
    # Il giudice deve sapere cosa l'utente ha chiesto di cambiare (forma, lingua moderna, rese
    # decise): altrimenti boccia come infedeltà proprio la trasformazione richiesta.
    contratto_giudice = "\n".join(
        p for p in (contract, nota_per_il_giudice(form, modernize), vincoli) if p
    )
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
                contratto_giudice, rewritten, target_language, client, judge, source=chunk_text
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
                        contratto_giudice, corrected, target_language, client, judge,
                        source=chunk_text,
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
        **continuity,  # il registro della lavorazione viaggia con la continuità
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


def _opzioni_corrispondono(run: RewriteRun, form: str, modernize: bool) -> bool:
    """La lavorazione trovata è lo stesso lavoro? Forma e modernizzazione ne fanno parte.

    Le lavorazioni nate prima di queste opzioni non le hanno registrate: erano tutte a forma
    conservata e lingua invariata, ed è così che si leggono.
    """
    opzioni = ((run.continuity or {}).get("registro") or {}).get("opzioni") or {}
    return (
        opzioni.get("form", FORM_KEEP) == form
        and bool(opzioni.get("modernize", False)) == bool(modernize)
    )


def _decisioni_precedenti(ses: Session, document_id: int, target_language: str | None) -> dict:
    """Le rese già decise dall'utente per questo libro in questa lingua, in lavorazioni passate.

    Chi ha deciso un modo di dire nell'anteprima non deve sentirselo richiedere traducendo il
    libro intero: la decisione riguarda il documento e la lingua, non la singola lavorazione.
    """
    runs = ses.exec(
        select(RewriteRun)
        .where(RewriteRun.document_id == document_id)
        .where(RewriteRun.mode == "translate")
        .where(RewriteRun.target_language == target_language)
        .order_by(RewriteRun.id.desc())
    ).all()
    ereditate: dict = {}
    for vecchia in runs:
        idiomi = (((vecchia.continuity or {}).get("registro") or {}).get("idiomi")) or {}
        for chiave, voce in idiomi.items():
            if chiave not in ereditate and voce.get("stato") == DECISA:
                ereditate[chiave] = {**copy.deepcopy(voce), "brani": []}
    return ereditate


def _stile_dell_originale(
    chunks: list[Chunk], contract: str, mode: str, client: Any, model: str | None
) -> dict:
    """Estrae lo stile del libro di partenza e ne fa il contratto della lavorazione.

    Ritorna la voce del registro: con ``contratto`` se l'estrazione è riuscita, senza (e con il
    motivo) se no — e allora si lavora col contratto ricevuto, come prima.
    """
    from bookwriter.style.engine import build_style_contract, extract_source_style

    testi = [c.text for c in chunks if has_prose(c.text)]
    try:
        profilo = extract_source_style(testi, client, name="Stile dell'originale", model=model)
    except LLMError as exc:
        return {"origine": "estrazione non riuscita", "errore": str(exc)}
    if not any(str(v).strip() for v in (profilo.dimensions or {}).values()):
        return {"origine": "estrazione non riuscita", "errore": "il modello non ha descritto lo stile"}
    verbo = "riproducilo nella lingua di arrivo" if mode == "translate" else "conservalo"
    contratto = (
        "Lo stile da seguire è quello dell'ORIGINALE, estratto automaticamente dal testo: "
        f"{verbo} — voce, ritmo, registro, lunghezza delle frasi, immagini.\n"
        + build_style_contract(profilo)
    )
    if contract.strip():
        contratto += "\n" + contract.strip()
    return {
        "origine": "estratto dall'originale",
        "nome": profilo.name,
        "profilo": profilo.model_dump(),
        "contratto": contratto,
    }


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
    literary: bool | None = None,
    idioms: bool | None = None,
    idiom_notes: bool = False,
    decide_idiom: Callable[[dict], Any] | None = None,
    auto_style: bool | None = None,
    polish: bool | None = None,
    form: str = FORM_KEEP,
    modernize: bool = False,
) -> RewriteReport:
    """Riscrive o traduce un documento brano per brano, in ordine, con ripresa deterministica.

    Opzioni della traduzione letteraria (tutte registrate nella lavorazione, vedi
    :func:`run_record`):

    - ``literary`` — interruttore generale; ``None`` vuol dire «sì se si traduce». Da lui
      dipendono i tre valori predefiniti qui sotto;
    - ``idioms`` — pre-passaggio sui modi di dire e i riferimenti culturali (solo traducendo).
      Le opzioni di resa si sottopongono all'utente con ``decide_idiom(voce)``, che riceve un
      dizionario (``chiave``, ``originale``, ``contesto``, ``tipo``, ``opzioni`` con ``tipo``/
      ``resa``/``spiegazione``, ``lingua``, ``book_pos``) e risponde con l'indice dell'opzione,
      con un testo libero, o con ``None`` per non decidere. È chiamata dal thread di lavoro:
      chi la passa dalla GUI deve portare la domanda nel thread dell'interfaccia e attendere.
      Senza risposta la voce resta «non decisa» e si decide dopo con :func:`answer_idioms`;
    - ``idiom_notes`` — fra le opzioni compare anche la nota del traduttore (di norma no);
    - ``auto_style`` — estrae lo stile dell'originale e lo usa come contratto; ``None`` vuol
      dire «sì se è letteraria e l'utente non ha scelto uno stile» (``style_profile_id``);
    - ``polish`` — passata finale di un editor madrelingua, sorvegliata (±15% di lunghezza,
      stessi paragrafi se l'impaginazione si conserva, stessi titoli e termini fissati);
    - ``form`` — ``"keep"`` conserva i paragrafi; ``"prose_from_verse"`` volge i versi in
      prosa: cambia numero e lunghezza dei paragrafi, non i capitoli (titoli);
    - ``modernize`` — grammatica e sintassi di oggi, conservando la voce dell'autore.
    """
    if form not in FORMS:
        raise ValueError(f"Forma sconosciuta: {form!r} (ammesse: {', '.join(FORMS)})")
    letteraria = (mode == "translate") if literary is None else bool(literary)
    con_idiomi = mode == "translate" and (letteraria if idioms is None else bool(idioms))
    con_rifinitura = letteraria if polish is None else bool(polish)
    con_stile_auto = (
        (letteraria and style_profile_id is None) if auto_style is None else bool(auto_style)
    )
    fattore = espansione(mode, form, modernize)

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
            # Per la stessa ragione ne fanno parte la forma (versi o prosa) e la modernizzazione.
            #
            # Con ``redo_stalled`` si riapre anche una lavorazione già chiusa: si sta correggendo
            # un libro consegnato, non riprendendone uno interrotto.
            stati = ("running", "done") if redo_stalled else ("running",)
            candidate = ses.exec(
                select(RewriteRun)
                .where(RewriteRun.document_id == document_id)
                .where(RewriteRun.kind == "full")
                .where(RewriteRun.mode == mode)
                .where(RewriteRun.target_language == target_language)
                .where(RewriteRun.style_profile_id == style_profile_id)
                .where(RewriteRun.status.in_(stati))
                .order_by(RewriteRun.id.desc())
            ).all()
            run = next((r for r in candidate if _opzioni_corrispondono(r, form, modernize)), None)
        if run is None:
            registro_nuovo: dict = {}
            if con_idiomi:
                registro_nuovo["idiomi"] = _decisioni_precedenti(ses, document_id, target_language)
            run = RewriteRun(
                document_id=document_id,
                style_profile_id=style_profile_id,
                kind=kind,
                mode=mode,
                target_language=target_language,
                ratio=ratio,
                status="running",
                continuity={"registro": registro_nuovo} if registro_nuovo else {},
            )
            ses.add(run)
            ses.commit()
            ses.refresh(run)
        run_id = run.id
        continuity = copy.deepcopy(dict(run.continuity or {}))
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

    # Il registro della lavorazione: cosa si è scelto e perché, da mostrare all'utente.
    registro: dict = continuity.setdefault("registro", {})
    registro["opzioni"] = {
        "form": form,
        "modernize": bool(modernize),
        "literary": letteraria,
        "idioms": con_idiomi,
        "idiom_notes": bool(idiom_notes),
        "polish": con_rifinitura,
        "auto_style": con_stile_auto,
    }
    idiomi_reg: dict = registro.setdefault("idiomi", {})
    errori_idiomi: dict = registro.setdefault("idiomi_errori", {})
    rifiniture: dict = registro.setdefault("rifinitura", {})

    todo = [c for c in targets if c.id not in done_ids]
    fermo_subito = bool(should_stop and should_stop())

    # Lo stile: quello scelto dall'utente, o quello dell'originale estratto una volta sola (e
    # conservato, così la ripresa non lo rilegge e non cambia contratto a metà libro).
    stile = registro.get("stile") or {}
    if not stile.get("contratto"):
        if con_stile_auto and not fermo_subito and any(has_prose(c.text) for c in todo):
            stile = _stile_dell_originale(chunks, contract, mode, client, model)
        elif style_profile_id is not None:
            stile = {"origine": "scelto dall'utente", "profilo_id": style_profile_id}
        elif not stile:
            stile = {"origine": "indicato nella richiesta"}
        registro["stile"] = stile
    contratto = stile.get("contratto") or contract

    opzioni_brano: dict[str, Any] = {
        "mode": mode, "target_language": target_language, "ratio": ratio, "model": model,
        "critic": critic, "critic_model": critic_model, "form": form, "modernize": modernize,
    }
    copied = stalled = unchecked = salvati = 0
    rifiniti = rifiniture_tenute = domande = 0
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
            # Modi di dire e riferimenti culturali: prima si elencano, poi decide l'utente.
            scelte: list[dict] = []
            if con_idiomi:
                try:
                    voci = trova_idiomi(chunk.text, target_language, client, model,
                                        note=idiom_notes)
                except LLMError as exc:
                    # Un elenco mancato non ferma la traduzione: il brano si traduce come si
                    # sarebbe tradotto senza, e la mancanza resta scritta nel registro.
                    voci = []
                    errori_idiomi[str(chunk.id)] = str(exc)
                scelte, chieste = registra_idiomi(
                    idiomi_reg, voci, chunk_id=chunk.id, book_pos=chunk.book_pos,
                    target_language=target_language, decide=decide_idiom,
                )
                domande += chieste
            vincoli = testo_delle_scelte(scelte)

            # Unità che stanno nel limite di una risposta, anche se l'uscita è più lunga.
            unita = unita_di_lavoro(chunk.text, fattore=fattore, form=form)
            pezzi: list[str] = []
            bloccate = recuperate = 0
            tutto_controllato = True
            esiti: list[str] = []
            for testo_unita in unita:
                try:
                    reso, continuity, checked = _rewrite_one(
                        testo_unita, contratto, continuity, client, vincoli=vincoli, **opzioni_brano
                    )
                except LLMDegenerate:
                    # Il modello si è bloccato a ripetere. Prima di rinunciare si riprova
                    # dividendo il brano: il blocco nasce quasi sempre dalla sua struttura, e le
                    # due metà separate di solito passano. Solo se fallisce anche così si
                    # conserva l'originale — che nel libro tradotto resta un pezzo nella lingua
                    # di partenza, ed è il difetto che questo tentativo serve a evitare.
                    ripreso = _riprova_dividendo(
                        testo_unita, contratto, continuity, client, vincoli=vincoli,
                        **opzioni_brano,
                    )
                    if ripreso is None:
                        pezzi.append(testo_unita)
                        bloccate += 1
                        continue
                    reso, continuity, checked = ripreso
                    recuperate += 1
                tutto_controllato = tutto_controllato and checked
                if con_rifinitura and reso.strip():
                    fissati = [*(continuity.get("glossary") or {}).values(),
                               *(s["resa"] for s in scelte)]
                    reso, esito = rifinisci(
                        reso, originale=testo_unita,
                        target_language=target_language if mode == "translate" else None,
                        vincolati=fissati, client=client, model=model,
                        conserva_paragrafi=form == FORM_KEEP,
                    )
                    esiti.append(esito)
                pezzi.append(reso)
            if bloccate == len(unita):
                rewritten = chunk.text
            else:
                rewritten = "\n\n".join(p for p in pezzi if p.strip())
            stalled += 1 if bloccate else 0
            salvati += 1 if recuperate else 0
            if bloccate < len(unita):
                unchecked += 0 if tutto_controllato else 1
            if esiti:
                rifiniture[str(chunk.id)] = esiti
                rifiniti += 1 if any(e == "applicata" for e in esiti) else 0
                rifiniture_tenute += 1 if any(not e.startswith("applicata") for e in esiti) else 0
        continuity["registro"] = registro
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
            db_run.continuity = copy.deepcopy(continuity)
            ses.add(db_run)
            ses.commit()
        if progress:
            progress(gia_fatti + idx + 1, len(targets))

    processed = len(done_ids) + len(todo) - fermato
    status = "done" if (kind == "full" and processed >= len(targets)) else "running"
    continuity["registro"] = registro
    with Session(engine) as ses:
        db_run = ses.get(RewriteRun, run_id)
        db_run.status = status
        # Il registro si salva anche se non si è lavorato nessun brano: le opzioni e lo stile
        # scelti valgono per la ripresa.
        db_run.continuity = copy.deepcopy(continuity)
        ses.add(db_run)
        ses.commit()
    if progress:
        progress(gia_fatti + len(todo) - fermato, len(targets))
    return RewriteReport(
        run_id=run_id, processed=processed, total=len(targets), status=status,
        copied=copied, stalled=stalled, unchecked=unchecked, salvaged=salvati,
        stopped=bool(fermato),
        polished=rifiniti, polish_kept=rifiniture_tenute, idioms_asked=domande,
        idioms_pending=sum(1 for v in idiomi_reg.values() if v.get("stato") != DECISA),
        style_source=str(stile.get("origine") or "") if (con_stile_auto or letteraria) else "",
        form=form, modernized=bool(modernize),
    )


def run_record(db_path: Path, run_id: int) -> dict:
    """Il registro di una lavorazione, da mostrare all'utente.

    Contiene: ``opzioni`` (forma, modernizzazione, passaggi attivi), ``stile`` (``origine``:
    «estratto dall'originale» / «scelto dall'utente» / «indicato nella richiesta» / «estrazione
    non riuscita»; con il ``profilo`` e il ``contratto`` usati quando è estratto), ``idiomi``
    (per chiave: originale, contesto, opzioni, ``stato`` «decisa»/«non decisa», ``resa``
    scelta, ``brani`` in cui compare), ``idiomi_errori`` e ``rifinitura`` (per brano, l'esito
    di ogni passata: «applicata», «scartata: …», «non riuscita: …»).
    """
    with Session(make_engine(db_path)) as ses:
        run = ses.get(RewriteRun, run_id)
        if run is None:
            return {}
        return copy.deepcopy(((run.continuity or {}).get("registro")) or {})


def idiom_decisions(db_path: Path, run_id: int, *, pending_only: bool = False) -> list[dict]:
    """I modi di dire e riferimenti culturali della lavorazione, decisi e no, in ordine di libro."""
    voci = run_record(db_path, run_id).get("idiomi") or {}
    fuori = [{"chiave": k, **v} for k, v in voci.items()]
    if pending_only:
        fuori = [v for v in fuori if v.get("stato") != DECISA]
    return sorted(fuori, key=lambda v: min(v.get("brani") or [0]))


def answer_idioms(db_path: Path, run_id: int, answers: dict[str, Any]) -> int:
    """Registra le decisioni date dopo (``chiave → indice d'opzione o testo``).

    I brani già tradotti in cui compare una voce appena decisa — o decisa diversamente — si
    tolgono dal fatto e la lavorazione torna «in corso»: riprendendola, solo quei brani si
    ritraducono, con la resa scelta. Ritorna quanti brani sono stati riaperti.
    """
    engine = make_engine(db_path)
    with Session(engine) as ses:
        run = ses.get(RewriteRun, run_id)
        if run is None:
            raise ValueError(f"Lavorazione {run_id} inesistente.")
        continuity = copy.deepcopy(dict(run.continuity or {}))
        registro = continuity.setdefault("registro", {})
        voci = registro.setdefault("idiomi", {})
        da_riaprire: set[int] = set()
        for chiave, risposta in answers.items():
            voce = voci.get(chiave)
            if voce is None:
                continue
            scelta = interpreta_scelta(voce, risposta)
            if scelta is None:
                continue
            cambiata = voce.get("stato") != DECISA or voce.get("resa") != scelta["resa"]
            voce.update(scelta, stato=DECISA, chiesta=True)
            if cambiata:
                da_riaprire.update(voce.get("brani") or [])
        righe = ses.exec(
            select(RewriteChunk).where(RewriteChunk.run_id == run_id)
        ).all() if da_riaprire else []
        riaperti = 0
        for rc in righe:
            if rc.chunk_id in da_riaprire:
                ses.delete(rc)
                riaperti += 1
        run.continuity = continuity
        if riaperti:
            run.status = "running"
        ses.add(run)
        ses.commit()
    return riaperti


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


def _allinea_per_sezioni(
    segmenti: list[int], titoli: set[int], paragrafi: list[str]
) -> tuple[dict[int, list[str]], bool]:
    """Allineamento per capitoli (titoli), non per paragrafi: è quello della prosa dai versi.

    Volgendo i versi in prosa i paragrafi cambiano di numero e di lunghezza per scelta, e
    ricucirli uno a uno sui versi sarebbe un non senso. Quel che non cambia sono i capitoli —
    canti, sezioni — cioè i titoli. Si accoppiano allora i titoli coi titoli, e dentro ogni
    sezione la prosa va nei segmenti di testo di quella sezione: un capoverso per segmento, i
    segmenti in avanzo spariscono, i capoversi in avanzo si accodano all'ultimo. Se i titoli non
    tornano, si ricade sull'allineamento semplice e il blocco si dichiara ricucito.
    """
    import re

    def e_titolo(p: str) -> bool:
        return bool(re.match(r"^\s{0,3}#{1,6}\s", p))

    seg_titoli = [i for i in segmenti if i in titoli]
    par_titoli = [p for p in paragrafi if e_titolo(p)]
    if len(seg_titoli) != len(par_titoli):
        return _align(segmenti, paragrafi)
    sezioni_seg: list[tuple[int | None, list[int]]] = [(None, [])]
    for i in segmenti:
        if i in titoli:
            sezioni_seg.append((i, []))
        else:
            sezioni_seg[-1][1].append(i)
    sezioni_par: list[tuple[str | None, list[str]]] = [(None, [])]
    for p in paragrafi:
        if e_titolo(p):
            sezioni_par.append((p, []))
        else:
            sezioni_par[-1][1].append(p)
    fuori: dict[int, list[str]] = {}
    storto = False
    for (titolo_seg, testi_seg), (titolo_par, testi_par) in zip(sezioni_seg, sezioni_par):
        if titolo_seg is not None and titolo_par is not None:
            fuori[titolo_seg] = [titolo_par]
        if testi_seg and testi_par:
            fuori.update(_align(testi_seg, testi_par)[0])
        elif testi_par or testi_seg:
            # Prosa senza versi a cui appoggiarsi, o versi rimasti senza prosa: la struttura del
            # blocco non torna, meglio l'allineamento semplice che perdere del testo.
            storto = True
    if storto:
        return _align(segmenti, paragrafi)
    return fuori, False


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
        opzioni = ((run.continuity or {}).get("registro") or {}).get("opzioni") or {}
    prosa = opzioni.get("form", FORM_KEEP) == FORM_PROSE_FROM_VERSE
    from bookwriter.layout.model import HEADING

    titoli = {i for i, s in enumerate(layout.segments) if s.kind == HEADING}

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
        if prosa:
            paragrafi = [p.strip() for p in rc.text.split("\n\n") if p.strip()]
            assegnati, storto = _allinea_per_sezioni(segmenti, titoli, paragrafi)
        else:
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
