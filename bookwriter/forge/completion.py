"""Completare un manoscritto: capitoli nuovi dove servono, parti mancanti dove mancano.

Il manoscritto è dell'autore e resta **parola per parola**: niente qui lo riscrive. Si aggiunge:

* **capitoli nuovi**, in qualunque punto — un seguito, un finale, un capitolo che manca fra due
  che ci sono; i file dei capitoli si rinumerano quando il piano è approvato
  (:func:`renumber`), mentre ``originale/`` conserva la numerazione del manoscritto;
* **parti mancanti** dentro un capitolo esistente rimasto a metà: si progettano come scene, con il
  paragrafo dopo cui vanno, e si inseriscono (:func:`complete_chapter`) fra il testo che c'è
  prima e quello che c'è dopo.

Le parti nuove si scrivono con la stessa macchina dei capitoli nuovi — piano delle scene,
registro della continuità, materiale di ricerca — e con la **voce del manoscritto**: lo stile
ricavato dal testo e un suo brano come esempio (:mod:`manuscript`).

Lo stato di ogni capitolo sta nella struttura (``status``): ``originale`` (non si tocca),
``da_completare`` → ``completato``, ``nuovo`` (si scrive come un capitolo qualunque).
"""

from __future__ import annotations

import json
from typing import Any

from bookwriter.forge import notebook as nb
from bookwriter.forge import scenes as sc
from bookwriter.forge.context import ContextPack
from bookwriter.forge.metrics import count_words
from bookwriter.forge.project import BookProject, ForgeState, slugify

ORIGINAL, TO_COMPLETE, COMPLETED, NEW = "originale", "da_completare", "completato", "nuovo"

_T = {"type": "string"}

COMPLETION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "chapters": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"title": _T, "does": _T, "words": {"type": "integer"},
                           "points": {"type": "array", "items": _T},
                           "source": {"type": "integer"},
                           "missing": {"type": "array", "items": _T}},
            "required": ["title", "does", "words", "points", "source", "missing"]}},
        "rationale": _T,
    },
    "required": ["chapters", "rationale"],
}


def propose(state: ForgeState, client: Any, summary: str, *, model: str | None = None
            ) -> dict[str, Any]:
    """Il piano del completamento: i capitoli originali, nell'ordine, e quelli nuovi fra loro.

    Il modello propone; il codice garantisce (:func:`merge_outline`) che ogni capitolo
    originale ci sia una volta sola e nel suo ordine — il manoscritto non si rimonta.
    """
    originali = [c for c in state.outline if c.get("source")]
    elenco = "\n".join(f"- [{c['source']}] «{c.get('title')}» ({c.get('words')} parole)"
                       for c in originali)
    sistema = (
        "Sei l'editor che progetta il completamento di un libro già in buona parte scritto. I "
        "capitoli esistenti restano tutti, nel loro ordine, e il loro testo non si riscrive: "
        "ognuno compare una volta con 'source' = il suo numero fra parentesi quadre. Fra loro, "
        "prima o dopo, metti i capitoli NUOVI che servono a completare il libro come vuole "
        "l'autore (quaderno e conversazione), con 'source' = 0. Per un capitolo esistente "
        "rimasto a metà, elenca in 'missing' le parti che mancano (una riga concreta ciascuna: "
        "chi fa cosa, dove); per gli altri 'missing' è vuoto. Ogni capitolo nuovo ha una "
        "funzione nel racconto ('does'), da 3 a 6 punti chiave (eventi e scene, non tesi) e una "
        "misura in parole simile a quella dei capitoli esistenti. Non anticipare né ripetere "
        "ciò che i capitoli esistenti già raccontano. In 'rationale' spiega in tre righe le "
        f"scelte. Scrivi in {nb.language_name(state)}."
    )
    utente = (f"# Il quaderno del libro\n{nb.brief(state)}\n\n"
              f"# Il manoscritto, capitolo per capitolo\n{summary}\n\n"
              f"# I capitoli esistenti (numero fra parentesi quadre)\n{elenco}")
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=COMPLETION_SCHEMA, model=model)
    except Exception:  # noqa: BLE001 - senza proposta il manoscritto resta com'è, e lo si dice
        r = {}
    return merge_outline(r if isinstance(r, dict) else {}, state.outline)


def merge_outline(proposta: dict[str, Any], attuale: list[dict[str, Any]]) -> dict[str, Any]:
    """Il piano proposto, riparato: ogni originale una volta, nel suo ordine, con i nuovi capitoli
    attaccati all'originale che li precede nella proposta."""
    originali = {int(c["source"]): c for c in attuale if c.get("source")}
    medi = [int(c.get("words") or 0) for c in originali.values()]
    misura = (sum(medi) // len(medi)) if medi else 3000
    in_testa: list[dict[str, Any]] = []
    dopo: dict[int, list[dict[str, Any]]] = {k: [] for k in originali}
    proposti: dict[int, dict[str, Any]] = {}
    ultimo = 0
    for voce in proposta.get("chapters") or []:
        if not isinstance(voce, dict):
            continue
        try:
            fonte = int(voce.get("source") or 0)
        except (TypeError, ValueError):
            fonte = 0
        if fonte in originali:
            # quello che il piano dice dell'originale vale sempre; la sua posizione no: i capitoli
            # nuovi si attaccano all'ultimo originale incontrato nell'ordine giusto
            proposti.setdefault(fonte, voce)
            ultimo = max(ultimo, fonte)
            continue
        nuovo = {"title": str(voce.get("title") or "").strip() or "Capitolo nuovo",
                 "does": str(voce.get("does") or "").strip(),
                 "points": [str(p).strip() for p in voce.get("points") or [] if str(p).strip()],
                 "words": int(voce.get("words") or 0) or misura, "status": NEW, "source": 0,
                 "part": 1}
        (dopo[ultimo] if ultimo else in_testa).append(nuovo)
    capitoli: list[dict[str, Any]] = list(in_testa)
    for k in sorted(originali):
        base = dict(originali[k])
        voce = proposti.get(k) or {}
        mancano = [str(m).strip() for m in voce.get("missing") or [] if str(m).strip()]
        base["does"] = str(voce.get("does") or base.get("does") or "").strip()
        base["points"] = mancano
        base["status"] = TO_COMPLETE if mancano else ORIGINAL
        if mancano:
            base["words"] = int(base.get("words") or 0) + max(
                0, int(voce.get("words") or 0) - int(base.get("words") or 0))
        capitoli.append(base)
        capitoli += dopo.get(k, [])
    for i, c in enumerate(capitoli, 1):
        c["number"] = i
    return {"parts": [{"number": 1, "title": ""}], "chapters": capitoli,
            "front_matter": [], "back_matter": [],
            "rationale": str(proposta.get("rationale") or "")}


def renumber(project: BookProject, state: ForgeState) -> list[tuple[str, str]]:
    """I file dei capitoli originali presi al numero che hanno nel piano. ``originale/`` resta
    com'è. Ritorna le rinomine fatte (vecchio, nuovo)."""
    rinomine: list[tuple[str, str]] = []
    temporanei = []
    for c in state.outline:
        file = str(c.get("file") or "")
        if not c.get("source"):
            continue
        attuale = project.chapters_dir / file if file else None
        if attuale is None or not attuale.exists():
            trovato = [p for p in project.chapter_files()
                       if p.stem.split("_", 1)[-1] == slugify(str(c.get("title") or ""))]
            attuale = trovato[0] if trovato else None
        if attuale is None:
            continue
        voluto = project.chapter_path(int(c["number"]), str(c.get("title") or ""))
        if attuale == voluto:
            c["file"] = voluto.name
            continue
        tmp = attuale.with_name(attuale.name + ".rinumera")
        attuale.rename(tmp)
        temporanei.append((tmp, voluto, c, attuale.name))
    for tmp, voluto, c, vecchio in temporanei:
        testo = tmp.read_text(encoding="utf-8")
        # l'intestazione del file segue il numero nuovo
        righe = testo.splitlines()
        if righe and righe[0].startswith("# "):
            righe[0] = f"# Capitolo {c['number']}: {c.get('title')}"
        voluto.write_text("\n".join(righe) + "\n", encoding="utf-8")
        tmp.unlink()
        c["file"] = voluto.name
        rinomine.append((vecchio, voluto.name))
    return rinomine


def pending(state: ForgeState, written: set[int]) -> list[dict[str, Any]]:
    """Cosa resta da scrivere: i capitoli nuovi senza file e i capitoli da completare."""
    return [c for c in state.outline
            if (c.get("status") == NEW and int(c.get("number") or 0) not in written)
            or c.get("status") == TO_COMPLETE]


# --------------------------------------------------------------------------- #
#  le parti mancanti di un capitolo esistente
# --------------------------------------------------------------------------- #
INSERT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {**{k: _T for k in sc.SCENE_FIELDS}, "claim_ids": {"type": "array",
                                                                          "items": _T},
                       "words": {"type": "integer"}, "after": {"type": "integer"}},
        "required": [*sc.SCENE_FIELDS, "claim_ids", "words", "after"]}}},
    "required": ["scenes"],
}


def _numbered(blocchi: list[str]) -> str:
    return "\n\n".join(f"[{i + 1}] {b}" for i, b in enumerate(blocchi))


def plan_insertions(client: Any, pack: ContextPack, blocchi: list[str], missing: list[str], *,
                    model: str | None = None) -> list[sc.Scene]:
    """Le scene che mancano a un capitolo esistente, ciascuna con il paragrafo dopo cui va."""
    sistema = (
        "Un capitolo di romanzo esiste ma è rimasto incompleto. NON si riscrive: si progettano "
        "soltanto le parti che mancano, come scene (obiettivo, ostacolo, indizio, decisione, "
        "conseguenza), ciascuna con 'after' = il numero del paragrafo dopo cui va inserita (0 = "
        "all'inizio del capitolo). Ogni scena si salda al testo che la precede e a quello che "
        "la segue, non ripete niente di quello che il capitolo già racconta e non anticipa i "
        f"capitoli dopo. Ogni campo è una riga breve. Scrivi in {pack.language}."
    )
    utente = "\n\n".join(p for p in (
        pack.chapter_block(),
        "# Le parti che mancano\n" + "\n".join(f"- {m}" for m in missing),
        pack.start_block(), pack.claims_block(), pack.decisions_block(),
        f"# Il capitolo com'è, a paragrafi numerati\n{_numbered(blocchi)}") if p)
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=INSERT_SCHEMA, model=model)
    except Exception:  # noqa: BLE001
        r = {}
    scene = []
    for i, d in enumerate((r.get("scenes") or []) if isinstance(r, dict) else []):
        s = sc._scene(d)
        try:
            s.after = str(max(0, min(len(blocchi), int(d.get("after") or 0))))
        except (TypeError, ValueError):
            s.after = str(len(blocchi))
        s.id = f"m{i + 1}"
        s.claim_ids = [k for k in s.claim_ids if pack.claim(k)]
        s.words = max(250, int(s.words or 600))
        if s.goal:
            scene.append(s)
    if not scene:  # il modello non ha progettato niente: una parte per riga, in fondo
        scene = [sc.Scene(id=f"m{i + 1}", goal=m, after=str(len(blocchi)), words=700)
                 for i, m in enumerate(missing)]
    return sorted(scene, key=lambda s: int(s.after))


def complete_chapter(project: BookProject, state: ForgeState, client: Any, chapter: dict[str, Any],
                     pack: ContextPack, *, model: str | None = None, style_contract: str = "",
                     cap: int = 1500, should_stop=None, on_section=None) -> dict[str, Any]:
    """Scrive le parti mancanti di un capitolo esistente e le inserisce. Il testo che c'era resta
    parola per parola. Ritorna ``{"path", "inserted": [(dopo, parole)], "words"}``."""
    from bookwriter.forge import writing as wr
    from bookwriter.forge.editor import parse

    numero = int(chapter.get("number") or 0)
    titolo = str(chapter.get("title") or "")
    percorso = project.chapter_path(numero, titolo)
    testo = percorso.read_text(encoding="utf-8")
    capitolo = parse(testo)
    blocchi = [p for s in capitolo.sections for p in s.paragraphs]
    mancano = [str(m) for m in chapter.get("points") or []]
    scene = plan_insertions(client, pack, blocchi, mancano, model=model)
    piano = sc.Plan(chapter=numero, title=titolo, goal=str(chapter.get("does") or ""),
                    scenes=scene, origin="completamento")
    sc.save(project, piano)
    nuovi: dict[int, list[str]] = {}
    inseriti = []
    for k, s in enumerate(scene):
        if should_stop and should_stop():
            break
        if on_section:
            on_section(k + 1, len(scene))
        dopo = int(s.after)
        seguito = " ".join(" ".join(blocchi[dopo:]).split()[:150])
        unita = sc.Unit(s.id, 1, 1, min(cap, s.words), insert=True)
        # il testo che viene prima del punto d'inserimento, con le parti già inserite
        gia = list(nuovi.get(0, []))
        for i in range(dopo):
            gia.append(blocchi[i])
            gia += nuovi.get(i + 1, [])
        parte = wr._write_unit(client, pack, piano, s, unita, ["\n\n".join(gia)], model=model,
                               style_contract=style_contract, titolo=titolo,
                               next_opening=seguito)
        nuovi.setdefault(dopo, []).append(parte)
        inseriti.append((dopo, count_words(parte)))
    # il capitolo ricomposto: ogni sezione con i suoi paragrafi, e le parti nuove al loro posto
    indice = 0
    for sezione in capitolo.sections:
        nuova = []
        if indice == 0 and 0 in nuovi:
            nuova += nuovi[0]
        for p in sezione.paragraphs:
            nuova.append(p)
            indice += 1
            nuova += nuovi.get(indice, [])
        sezione.paragraphs = nuova
    finale = capitolo.render()
    percorso.write_text(finale, encoding="utf-8")
    chapter["status"] = COMPLETED
    return {"path": percorso, "inserted": inseriti, "words": count_words(finale)}


def completion_markdown(outline: list[dict[str, Any]]) -> str:
    """Il piano del completamento in forma leggibile, per la specifica."""
    righe = ["## Piano del completamento", "",
             "Il testo dei capitoli originali resta parola per parola; si aggiungono i capitoli "
             "nuovi e le parti mancanti.", ""]
    for c in outline:
        stato = {ORIGINAL: "originale, invariato", TO_COMPLETE: "originale, da completare",
                 COMPLETED: "originale, completato", NEW: "NUOVO"}.get(c.get("status"), "")
        righe.append(f"- **{c.get('number')}. {c.get('title')}** — {stato}"
                     + (f" (era il {c['source']} del manoscritto)" if c.get("source")
                        and c.get("source") != c.get("number") else ""))
        for p in c.get("points") or []:
            righe.append(f"  - {'manca: ' if c.get('status') == TO_COMPLETE else ''}{p}")
    return "\n".join(righe)


def dump(outline: list[dict[str, Any]]) -> str:
    return json.dumps(outline, ensure_ascii=False, indent=1)
