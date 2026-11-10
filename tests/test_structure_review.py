"""Le operazioni strutturali sul libro intero: diagnosi, decisione dell'autore, applicazione,
annullamento.

L'autore le ha volute dopo la verifica della qualità del libro: i difetti di struttura (due
capitoli che fanno lo stesso lavoro, un effetto raccontato prima della causa) non si correggono
dentro un capitolo. Qui si controlla che il modello veda solo le schede, mai i capitoli interi;
che i controlli deterministici fermino un riordino o un taglio che rompe le dipendenze; che niente
si applichi senza un sì esplicito; che file, dati, registro e specifica si rinumerino insieme; e
che l'annullamento rimetta il libro esattamente com'era.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from bookwriter.config import AppConfig
from bookwriter.forge import ledger as lg
from bookwriter.forge import quality as ql
from bookwriter.forge import spec as sp
from bookwriter.forge import structure_review as sr
from bookwriter.forge.engine import Forge
from bookwriter.forge.project import planned_chapters

#: Una frase che sta solo nel corpo dei capitoli: non deve mai arrivare al modello.
SEGRETO = "frase-del-corpo-che-il-modello-non-deve-leggere"

TITOLI = ["L'arrivo", "Il fascicolo", "La copia", "La notte", "La chiave"]
#: chi presenta cosa: il fascicolo Kappa nasce nel 2 e torna nel 3; la chiave d'ottone è solo del 5
INVENZIONI = {2: ["fascicolo Kappa"], 5: ["chiave d'ottone"]}
DATE = {1: "3 marzo 2019, mattina", 2: "5 marzo 2019, sera", 3: "7 marzo 2019, notte",
        4: "9 marzo 2019, alba", 5: "12 marzo 2019, pomeriggio"}


def _testo(n: int, titolo: str) -> str:
    extra = {2: "Marco apre il fascicolo Kappa per la prima volta.",
             3: "Marco fotografa il fascicolo Kappa pagina per pagina.",
             5: "Sotto il pavimento c'è una chiave d'ottone."}.get(n, "")
    corpo = " ".join(f"Frase {k} del capitolo {n}, con un dettaglio preciso." for k in range(40))
    return (f"# Capitolo {n}: {titolo}\n\n## Apertura {n}\n\nPrimo paragrafo del capitolo {n}. "
            f"{extra} {SEGRETO}.\n\nSecondo paragrafo del capitolo {n}. {corpo}\n\n"
            f"## Seguito {n}\n\nTerzo paragrafo del capitolo {n}. {corpo}\n\n"
            f"Ultimo paragrafo del capitolo {n}.\n\n## Note bibliografiche\n\n"
            f"Rossi, M. ({2000 + n}). *Opera {n}*. Editore.\n")


def _libro(tmp_path: Path, client=None) -> Forge:
    f = Forge(tmp_path / "book", AppConfig(), client=client)
    f.start()
    f.state.notebook["form"] = "romanzo"
    f.state.outline = [{"number": n, "title": t, "does": f"Il capitolo {n} fa la sua parte.",
                        "words": 900, "part": 1, "points": [f"punto del {n}"]}
                       for n, t in enumerate(TITOLI, 1)]
    registro = lg.Ledger()
    for n, t in enumerate(TITOLI, 1):
        testo = _testo(n, t)
        path = f.project.chapter_path(n, t)
        path.write_text(testo, encoding="utf-8")
        registro.chapters[n] = lg.ChapterEntry(
            number=n, title=t, source_hash=lg.text_hash(testo), words=500,
            summary=f"Riassunto del capitolo {n}.",
            end_state=lg.EndState(place=f"luogo {n}", time=DATE[n]),
            told=[f"fatto {n}"],
            inventions=[{"name": x, "what": "", "status": "fiction"}
                        for x in INVENZIONI.get(n, [])])
        # i dati che seguono il capitolo
        f.project.research_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{n:02d}_{sr.slugify(t)}"
        (f.project.research_dir / f"{stem}.md").write_text(f"Ricerca del capitolo {n}.",
                                                           encoding="utf-8")
        (f.project.research_dir / f"{stem}.json").write_text(json.dumps(
            {"number": n, "title": t, "questions": [{"id": f"q{n}", "chapter": n}]}),
            encoding="utf-8")
        f.project.plans_dir.mkdir(parents=True, exist_ok=True)
        (f.project.plans_dir / f"{stem}.json").write_text(json.dumps({"chapter": n, "title": t}),
                                                          encoding="utf-8")
        ql.save_round(f.project, path.stem, ql.Evaluation(chapter=n, title=t,
                                                          text_hash=ql._hash(testo)))
        ql.save_version(f.project, path.stem, testo, "originale")
    lg.save(f.project, registro)
    (f.project.research_dir / "review.json").write_text(json.dumps(
        {"points": [{"id": "p4", "chapter": 4}, {"id": "p5", "chapter": 5},
                    {"id": "p0", "chapter": 0}]}), encoding="utf-8")
    sp.write_spec(f.project, f.state, {"chapters": f.state.outline,
                                       "parts": [{"number": 1, "title": ""}],
                                       "front_matter": ["Introduzione"], "back_matter": []})
    f.save()
    return f


def _op(kind: str, chapters: list[int], **kw) -> dict:
    base = {"kind": kind, "chapters": chapters, "after": 0, "destination": 0,
            "unique_content": [], "new_titles": [], "split_at": "", "issue": "un problema",
            "evidence": "le schede", "reason": "", "risk": "", "breaks": []}
    return {**base, **kw}


class Strutturista:
    """Propone le operazioni che gli si danno; nella fusione riscrive la giunzione."""

    def __init__(self, operazioni: list[dict] | None = None, *, giunzione: bool = True,
                 guasto: bool = False) -> None:
        self.operazioni = list(operazioni or [])
        self.giunzione, self.guasto = giunzione, guasto
        self.messaggi: list[list[dict]] = []
        self.giunzioni: list[str] = []

    def chat_json(self, messages, *, schema, model=None):
        self.messaggi.append(messages)
        if self.guasto:
            raise RuntimeError("il server non risponde")
        if "operations" in schema.get("properties", {}):
            return {"assessment": "Due capitoli fanno lo stesso lavoro.",
                    "operations": copy.deepcopy(self.operazioni)}
        return {}

    def chat(self, messages, *, model=None, **kw):
        self.giunzioni.append(messages[-1]["content"])
        if not self.giunzione:
            raise RuntimeError("il server non risponde")
        n = int(re.search(r"circa (\d+) parole", messages[0]["content"]).group(1))
        return "GIUNZIONE " + " ".join(["parola"] * max(1, n - 1))


def _files(root: Path) -> dict[str, bytes]:
    """Tutto il libro su disco, tranne le copie di sicurezza e il file delle decisioni."""
    fuori = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith("story/struttura/") \
                and rel != "story/struttura_review.json" and not rel.startswith(".forge/"):
            fuori[rel] = p.read_bytes()
    return fuori


# --------------------------------------------------------------------------- #
#  la diagnosi
# --------------------------------------------------------------------------- #
def test_the_diagnosis_reads_the_cards_not_the_chapters(tmp_path: Path) -> None:
    modello = Strutturista([
        _op("reorder", [4], after=1, reason="la notte viene prima della copia"),
        _op("reorder", [4], after=1),  # doppia
        _op("reorder", [3], after=1),  # il 3 usa il fascicolo Kappa, presentato nel 2
        _op("cut", [5], unique_content=["la chiave d'ottone"]),  # senza destinazione
        _op("merge", [2, 1], new_titles=["L'arrivo e il fascicolo"]),
        _op("reorder", [99], after=1),  # un capitolo che non c'è
        _op("split", [3], split_at="Seguito 3", new_titles=["La copia", "Il seguito"]),
        _op("riordina", [2], after=2),  # dopo sé stesso
    ])
    f = _libro(tmp_path, modello)
    giro = f.structure_review()
    assert len(modello.messaggi) == 1, "un libro corto si guarda in una chiamata"
    inviato = json.dumps(modello.messaggi[0], ensure_ascii=False)
    assert SEGRETO not in inviato, "al modello vanno le schede, non il testo"
    assert "Riassunto del capitolo 3" in inviato and "fascicolo Kappa" in inviato
    assert "Sezioni: Apertura 3 | Seguito 3" in inviato
    tipi = [(op.kind, op.chapters) for op in giro.operations]
    assert tipi == [("reorder", [4]), ("reorder", [3]), ("cut", [5]), ("merge", [1, 2]),
                    ("split", [3])]
    riordino, sbagliato, taglio, fusione, divisione = giro.operations
    assert riordino.blockers == [] and any("cronologia" in a for a in riordino.warnings)
    assert any("fascicolo Kappa" in b for b in sbagliato.blockers)
    assert any("nessuna destinazione" in b for b in taglio.blockers)
    assert any("chiave d'ottone" in a for a in taglio.warnings)
    assert fusione.blockers == [] and divisione.blockers == []
    assert all(op.status == sr.PROPOSTA for op in giro.operations)
    salvato = json.loads(sr.data_path(f.project).read_text(encoding="utf-8"))
    assert salvato["format"] == sr.FORMAT and salvato["revision"] == 1
    assert salvato["rounds"][0]["operations"][0]["refs"] == [
        {"number": 4, "title": "La notte"}, {"number": 1, "title": "L'arrivo"}]
    assert giro.quality.startswith("nessuna valutazione del libro")


def test_a_long_book_is_seen_in_windows_with_short_answers(tmp_path: Path) -> None:
    modello = Strutturista()
    f = _libro(tmp_path, modello)
    f.state.outline += [{"number": n, "title": f"Capitolo {n}", "words": 900, "part": 1}
                        for n in range(6, 15)]
    f.save()
    f.structure_review()
    # una passata d'insieme (schede corte) e due finestre (1-10, 9-14)
    assert len(modello.messaggi) == 3
    insieme = modello.messaggi[0][-1]["content"]
    assert "Il libro intero, in breve" in insieme and "Cosa deve fare" not in insieme
    assert "capitoli 1–10" in modello.messaggi[1][-1]["content"]
    assert "capitoli 9–14" in modello.messaggi[2][-1]["content"]


def test_a_model_failure_does_not_stop_the_diagnosis(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista(guasto=True))
    giro = f.structure_review()
    assert giro.operations == [] and giro.errors and "non risponde" in giro.errors[0]
    assert sr.load(f.project).rounds[0].errors == giro.errors


def test_a_rejected_proposal_is_not_proposed_again(tmp_path: Path) -> None:
    modello = Strutturista([_op("reorder", [4], after=1), _op("merge", [1, 2])])
    f = _libro(tmp_path, modello)
    primo = f.structure_review()
    f.structure_decide(primo.operations[0].id, "reject", note="la notte resta dov'è")
    f.structure_decide(primo.operations[1].id, "accept")
    secondo = f.structure_review()
    assert secondo.operations == [], "rifiutata e accettata non tornano come nuove"
    assert any("già rifiutate" in n for n in secondo.notes)
    dati = f.structure_data()
    assert dati.operation(primo.operations[1].id).status == sr.ACCETTATA, "la decisione resta"


# --------------------------------------------------------------------------- #
#  la decisione
# --------------------------------------------------------------------------- #
def test_a_blocked_operation_needs_a_motivated_waiver(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista([_op("cut", [5], unique_content=["la chiave"])]))
    op = f.structure_review().operations[0]
    with pytest.raises(ValueError, match="deroga"):
        f.structure_decide(op.id, "accept")
    # una destinazione scioglie il blocco
    deciso = f.structure_decide(op.id, "accept", destination=3)
    assert deciso.status == sr.ACCETTATA and deciso.blockers == [] and not deciso.waiver
    assert {"number": 3, "title": "La copia"} in deciso.refs
    f.structure_decide(op.id, "reset")
    rimesso = f.structure_decide(op.id, "accept", destination=0, waiver="la chiave non serve")
    assert rimesso.waiver == "la chiave non serve"
    assert [r["what"].split(":")[0] for r in rimesso.log][-1] == "accettata con deroga"


def test_nothing_is_applied_without_acceptance(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista([_op("reorder", [4], after=1)]))
    op = f.structure_review().operations[0]
    prima = _files(f.project.root)
    with pytest.raises(ValueError, match="accettata"):
        f.structure_apply(op.id)
    assert _files(f.project.root) == prima


# --------------------------------------------------------------------------- #
#  l'applicazione e l'annullamento
# --------------------------------------------------------------------------- #
def test_a_reorder_renumbers_chapters_and_everything_that_follows_them(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista([_op("reorder", [4], after=1), _op("merge", [2, 3])]))
    giro = f.structure_review()
    riordino, fusione = giro.operations
    f.structure_decide(riordino.id, "accept")
    esito = f.structure_apply(riordino.id)
    p = f.project
    assert [c["title"] for c in f.state.outline] == ["L'arrivo", "La notte", "Il fascicolo",
                                                     "La copia", "La chiave"]
    assert [c["number"] for c in f.state.outline] == [1, 2, 3, 4, 5]
    assert f.project.load_state().outline == f.state.outline, "lo stato è salvato"
    scritti = p.written_chapters()
    assert scritti[2].name == "02_la-notte.md"
    testo = scritti[2].read_text(encoding="utf-8")
    assert testo.startswith("# Capitolo 2: La notte") and "Primo paragrafo del capitolo 4" in testo
    assert len(p.chapter_files()) == 5
    # la ricerca, il piano e la storia della qualità seguono il capitolo
    ricerca = json.loads((p.research_dir / "02_la-notte.json").read_text(encoding="utf-8"))
    assert ricerca["number"] == 2 and ricerca["questions"][0]["chapter"] == 2
    assert (p.research_dir / "02_la-notte.md").read_text(encoding="utf-8") == \
        "Ricerca del capitolo 4."
    assert not (p.research_dir / "04_la-notte.json").exists()
    assert json.loads((p.plans_dir / "02_la-notte.json").read_text(encoding="utf-8"))[
        "chapter"] == 2
    assert ql.versions(p, "02_la-notte") and not ql.versions(p, "04_la-notte")
    # «La copia» (3 → 4) ha davanti lo stesso capitolo: la sua valutazione vale ancora
    copia = scritti[4]
    assert ql.latest(p, copia.stem).text_hash == ql._hash(copia.read_text(encoding="utf-8"))
    notte = ql.latest(p, "02_la-notte")
    assert notte.text_hash != ql._hash(testo), "spostata: si rivaluta"
    # il registro: rinumerato, e valido per chi ha solo cambiato numero
    registro = lg.load(p)
    assert registro.entry(2).summary == "Riassunto del capitolo 4."
    assert registro.entry(2).source_hash == lg.text_hash(testo)
    # la revisione della ricerca, la specifica
    punti = json.loads((p.research_dir / "review.json").read_text(encoding="utf-8"))["points"]
    assert [x["chapter"] for x in punti] == [2, 5, 0]
    assert planned_chapters(p.spec_path) == {1: "L'arrivo", 2: "La notte", 3: "Il fascicolo",
                                             4: "La copia", 5: "La chiave"}
    spec = p.spec_path.read_text(encoding="utf-8")
    assert "#### Capitolo 2 — La notte" in spec and "**Pagine iniziali:** Introduzione" in spec
    # cosa ricontrollare, la copia di sicurezza, le altre proposte
    assert esito.run.recheck == [2, 3, 5]
    dati = f.structure_data()
    assert dati.recheck == [2, 3, 5]
    assert (p.root / esito.run.backup / "files" / "chapters" / "04_la-notte.md").exists()
    rinumerata = dati.operation(fusione.id)
    assert rinumerata.chapters == [3, 4] and rinumerata.status == sr.PROPOSTA
    assert rinumerata.refs == [{"number": 3, "title": "Il fascicolo"},
                               {"number": 4, "title": "La copia"}]
    assert dati.operation(riordino.id).status == sr.APPLICATA


def test_undo_puts_the_book_back_exactly_as_it_was(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista([_op("reorder", [4], after=1), _op("merge", [2, 3])]))
    giro = f.structure_review()
    prima, struttura = _files(f.project.root), copy.deepcopy(f.state.outline)
    f.structure_decide(giro.operations[0].id, "accept")
    f.structure_apply(giro.operations[0].id)
    assert _files(f.project.root) != prima
    esito = f.structure_undo()
    assert _files(f.project.root) == prima
    assert f.state.outline == struttura and f.project.load_state().outline == struttura
    dati = f.structure_data()
    assert dati.operation(giro.operations[0].id).status == sr.ANNULLATA
    assert dati.operation(giro.operations[1].id).chapters == [2, 3], "rinumerata indietro"
    assert dati.recheck == [] and dati.runs[-1].undone and esito.restored
    assert not (f.project.story_dir / "versions" / "02_la-notte").exists(), "niente cartelle vuote"
    with pytest.raises(ValueError, match="niente da annullare"):
        f.structure_undo()


def test_undo_refuses_to_erase_work_done_afterwards(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista([_op("reorder", [4], after=1)]))
    op = f.structure_review().operations[0]
    f.structure_decide(op.id, "accept")
    f.structure_apply(op.id)
    capitolo = f.project.written_chapters()[2]
    capitolo.write_text(capitolo.read_text(encoding="utf-8") + "\nUna revisione.\n",
                        encoding="utf-8")
    with pytest.raises(ValueError, match="02_la-notte.md"):
        f.structure_undo()
    f.structure_undo(force=True)
    assert f.project.written_chapters()[4].name == "04_la-notte.md"


def test_a_merge_rewrites_only_the_seam(tmp_path: Path) -> None:
    modello = Strutturista([_op("merge", [1, 2], new_titles=["L'arrivo e il fascicolo"]),
                            _op("reorder", [5], after=3)])
    f = _libro(tmp_path, modello)
    giro = f.structure_review()
    f.structure_decide(giro.operations[0].id, "accept")
    esito = f.structure_apply(giro.operations[0].id)
    assert len(modello.giunzioni) == 1
    giunzione = modello.giunzioni[0]
    assert "Ultimo paragrafo del capitolo 1" in giunzione and "Primo paragrafo del capitolo 2" \
        in giunzione and "Secondo paragrafo del capitolo 1" not in giunzione
    scritti = f.project.written_chapters()
    assert len(scritti) == 4
    unito = scritti[1].read_text(encoding="utf-8")
    assert unito.startswith("# Capitolo 1: L'arrivo e il fascicolo")
    assert "Primo paragrafo del capitolo 1" in unito and "GIUNZIONE" in unito
    assert "Ultimo paragrafo del capitolo 2" in unito
    assert "Primo paragrafo del capitolo 2" not in unito, "riscritto nella giunzione"
    assert unito.count("## Note bibliografiche") == 1 and "Opera 2" in unito
    assert scritti[2].read_text(encoding="utf-8").startswith("# Capitolo 2: La copia")
    assert f.state.outline[0]["words"] == 1800
    assert f.state.outline[0]["points"] == ["punto del 1", "punto del 2"]
    registro = lg.load(f.project)
    assert registro.entry(1).source_hash == "" and "capitolo 2" in registro.entry(1).summary
    assert 1 in esito.run.recheck
    assert (f.project.research_dir / "01_larrivo-e-il-fascicolo.md").read_text(
        encoding="utf-8") == "Ricerca del capitolo 1.\n\nRicerca del capitolo 2."
    assert not (f.project.plans_dir / "01_larrivo-e-il-fascicolo.json").exists()
    altra = f.structure_data().operation(giro.operations[1].id)
    assert (altra.chapters, altra.after) == ([4], 2)


def test_without_a_seam_the_texts_are_joined_as_they_are(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista([_op("merge", [3, 4])], giunzione=False))
    op = f.structure_review().operations[0]
    f.structure_decide(op.id, "accept")
    esito = f.structure_apply(op.id)
    unito = f.project.written_chapters()[3].read_text(encoding="utf-8")
    assert "Ultimo paragrafo del capitolo 3" in unito and "Primo paragrafo del capitolo 4" in unito
    assert any("giunzione" in a for a in esito.run.warnings)


def test_a_cut_keeps_the_unique_content_somewhere(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista([_op("cut", [4], unique_content=["l'alba sul porto"],
                                           destination=3)]))
    op = f.structure_review().operations[0]
    assert op.blockers == []
    f.structure_decide(op.id, "accept")
    esito = f.structure_apply(op.id)
    p = f.project
    assert [c["title"] for c in f.state.outline] == ["L'arrivo", "Il fascicolo", "La copia",
                                                     "La chiave"]
    assert "(dal capitolo tolto «La notte») l'alba sul porto" in f.state.outline[2]["points"]
    assert not any("notte" in x.name for x in p.chapter_files())
    assert (p.root / esito.run.backup / "files" / "chapters" / "04_la-notte.md").exists()
    assert not (p.research_dir / "04_la-notte.json").exists()
    punti = json.loads((p.research_dir / "review.json").read_text(encoding="utf-8"))["points"]
    assert [x["chapter"] for x in punti] == [3, 4, 0], "i punti del tagliato: alla destinazione"
    dati = f.structure_data()
    assert dati.integrations == [{"op": op.id, "chapter": 3, "from": "La notte",
                                  "content": ["(dal capitolo tolto «La notte») l'alba sul porto"]}]
    assert 3 in dati.recheck


def test_a_cut_that_loses_an_introduction_is_blocked(tmp_path: Path) -> None:
    # il 2 presenta il fascicolo Kappa che il 3 usa: tagliarlo verso il 4 è troppo tardi
    f = _libro(tmp_path, Strutturista([_op("cut", [2], destination=4,
                                           unique_content=["il fascicolo"])]))
    op = f.structure_review().operations[0]
    assert any("troppo tardi" in b and "fascicolo Kappa" in b for b in op.blockers)


def test_a_split_makes_two_chapters_at_a_section(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista([_op("split", [3], split_at="## Seguito 3",
                                           new_titles=["La copia", "Dopo la copia"])]))
    op = f.structure_review().operations[0]
    f.structure_decide(op.id, "accept")
    f.structure_apply(op.id)
    scritti = f.project.written_chapters()
    assert len(scritti) == 6
    uno, due = scritti[3].read_text(encoding="utf-8"), scritti[4].read_text(encoding="utf-8")
    assert uno.startswith("# Capitolo 3: La copia") and "## Seguito 3" not in uno
    assert due.startswith("# Capitolo 4: Dopo la copia") and "## Seguito 3" in due
    assert "Opera 3" in uno and "Opera 3" in due
    assert scritti[5].name == "05_la-notte.md"
    assert [c["title"] for c in f.state.outline][2:4] == ["La copia", "Dopo la copia"]
    assert (f.project.research_dir / "04_dopo-la-copia.json").exists()


def test_a_stale_proposal_is_not_applied(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista([_op("reorder", [4], after=1)]))
    op = f.structure_review().operations[0]
    f.structure_decide(op.id, "accept")
    f.state.outline[3]["title"] = "Un altro titolo"
    with pytest.raises(ValueError, match="cambiata"):
        f.structure_apply(op.id)


# --------------------------------------------------------------------------- #
#  dopo: integrare, ricontrollare
# --------------------------------------------------------------------------- #
def test_integration_is_a_transaction(tmp_path: Path, monkeypatch) -> None:
    from bookwriter.forge import completion as cm

    f = _libro(tmp_path, Strutturista([_op("cut", [4], unique_content=["l'alba"],
                                           destination=3)]))
    op = f.structure_review().operations[0]
    f.structure_decide(op.id, "accept")
    f.structure_apply(op.id)
    destinazione = f.project.written_chapters()[3]
    prima = destinazione.read_text(encoding="utf-8")
    monkeypatch.setattr(f, "chapter_tools", lambda *a, **k: type("T", (), {"pack": None})())

    def guasto(project, state, client, chapter, pack, **kw):
        destinazione.write_text(prima + "\nmezzo lavoro\n", encoding="utf-8")
        raise RuntimeError("il server non risponde")

    monkeypatch.setattr(cm, "complete_chapter", guasto)
    with pytest.raises(RuntimeError):
        f.structure_integrate(op.id)
    assert destinazione.read_text(encoding="utf-8") == prima, "tutto com'era"

    visti = {}

    def riuscito(project, state, client, chapter, pack, **kw):
        visti["points"] = chapter["points"]
        destinazione.write_text(prima + "\nL'alba sul porto.\n", encoding="utf-8")
        return {"path": destinazione, "inserted": [(3, 5)], "words": 0}

    monkeypatch.setattr(cm, "complete_chapter", riuscito)
    run = f.structure_integrate(op.id)
    assert visti["points"] == ["(dal capitolo tolto «La notte») l'alba"]
    assert f.state.outline[2]["points"][-1].endswith("l'alba"), "la struttura non cambia"
    dati = f.structure_data()
    assert dati.integrations == [] and run.kind == "integrate" and 3 in dati.recheck
    f.structure_undo()
    assert destinazione.read_text(encoding="utf-8") == prima
    assert f.structure_data().integrations, "si può integrare di nuovo"


def test_recheck_rebuilds_the_ledger_and_reevaluates_marked_chapters(tmp_path: Path,
                                                                     monkeypatch) -> None:
    f = _libro(tmp_path, Strutturista([_op("reorder", [4], after=1)]))
    op = f.structure_review().operations[0]
    f.structure_decide(op.id, "accept")
    f.structure_apply(op.id)
    chiamate = {"ledger": 0, "quality": []}
    monkeypatch.setattr(f, "ledger_rebuild",
                        lambda **kw: chiamate.__setitem__("ledger", chiamate["ledger"] + 1))
    monkeypatch.setattr(f, "evaluate_chapter_quality",
                        lambda n, **kw: chiamate["quality"].append(n))
    assert f.structure_recheck() == [2, 3, 5]
    assert chiamate == {"ledger": 1, "quality": [2, 3, 5]}
    assert f.structure_data().recheck == []


def test_retitle_keeps_the_form_of_the_heading() -> None:
    assert sr.retitle("# Capitolo 3: La copia\n\nTesto.", 2, "La copia") == \
        "# Capitolo 2: La copia\n\nTesto."
    assert sr.retitle("# Chapter IV. The night\n\nText.", 2, "The night").startswith(
        "# Chapter II. The night")
    assert sr.retitle("# La copia\n\nTesto.", 2, "La copia") == "# La copia\n\nTesto."
    assert sr.retitle("# La copia\n\nTesto.", 2, "Altro", new_title=True).startswith("# Altro")
    assert sr.retitle("Testo senza titolo.", 2, "X") == "Testo senza titolo."


# --------------------------------------------------------------------------- #
#  i casi al margine
# --------------------------------------------------------------------------- #
def test_an_impossible_operation_cannot_be_waived(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista([_op("split", [3], split_at="Una sezione che non c'è")]))
    op = f.structure_review().operations[0]
    assert any("non è fra quelle" in b for b in op.blockers)
    with pytest.raises(ValueError, match="non si può applicare"):
        f.structure_decide(op.id, "accept", waiver="fidati")


def test_an_incomplete_diagnosis_keeps_the_open_proposals(tmp_path: Path) -> None:
    modello = Strutturista([_op("reorder", [4], after=1)])
    f = _libro(tmp_path, modello)
    primo = f.structure_review()
    modello.guasto = True
    secondo = f.structure_review()
    assert any("incompleta" in n for n in secondo.notes)
    assert f.structure_data().operation(primo.operations[0].id).status == sr.PROPOSTA
    modello.guasto, modello.operazioni = False, []
    f.structure_review()
    assert f.structure_data().operation(primo.operations[0].id).status == sr.SUPERATA


def test_a_decision_taken_while_the_model_thinks_is_kept(tmp_path: Path) -> None:
    modello = Strutturista([_op("merge", [1, 2])])
    f = _libro(tmp_path, modello)
    primo = f.structure_review().operations[0]
    originale = modello.chat_json

    def mentre_pensa(messages, *, schema, model=None):
        sr.decide(f.project, f.state, primo.id, "accept")  # l'autore, nella finestra
        return originale(messages, schema=schema, model=model)

    modello.chat_json = mentre_pensa
    modello.operazioni = [_op("reorder", [4], after=1)]
    secondo = f.structure_review()
    dati = f.structure_data()
    assert dati.operation(primo.id).status == sr.ACCETTATA
    assert [op.id for op in dati.rounds[-1].operations] == [op.id for op in secondo.operations]


def test_common_objects_are_not_dependencies(tmp_path: Path) -> None:
    f = _libro(tmp_path, Strutturista())
    registro = lg.load(f.project)
    registro.chapters[3].end_state.objects = ["paragrafo", "dettaglio preciso"]
    lg.save(f.project, registro)
    dip = sr.dependencies(sr.chapter_views(f.project, f.state))
    assert "paragrafo" not in dip.users, "una parola comune non è un nome del libro"
    assert dip.users["dettaglio preciso"] == {1, 2, 3, 4, 5}
    assert dip.introduced_by(2) == ["fascicolo kappa"]
