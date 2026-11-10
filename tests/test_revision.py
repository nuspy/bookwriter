"""La revisione di un capitolo come la chiede la specifica editoriale del 25/10/2026.

Diagnosi, piano, intervento e verifica separati; rilievi con ID stabili e stato; interventi con
l'operazione e il bersaglio; gate pass/fail che nessuna media compensa; verdetto; report in dodici
sezioni; la continuità controllata sui capitoli **originali** dell'app, e dichiarata.
"""

from __future__ import annotations

import re
from pathlib import Path

from bookwriter.config import AppConfig
from bookwriter.forge import quality as ql
from bookwriter.forge import revision as rv
from bookwriter.forge import timeline as tl
from bookwriter.forge.engine import Forge

BOZZA = (
    "# Capitolo 2: La porta\n\n"
    "## Il volo\n\n"
    "Nicolò lascia il Cairo alle 21:40 con il fascicolo nella borsa.\n\n"
    "All'aeroporto controlla due volte la carta d'imbarco e il fascicolo.\n\n"
    "## Il cassetto\n\n"
    "Nell'archivio la direttrice apre il cassetto e gli mostra le fiale.\n\n"
    "Il sistema archivia tutto, anche le persone, anche lui.\n\n"
    "## L'archivio\n\n"
    "Nell'archivio il direttore apre il cassetto e gli mostra le fiale.\n\n"
    "Il sistema archivia tutto, ancora una volta.\n"
)


class Revisore:
    """Un modello finto che sa fare ogni passo della revisione."""

    def __init__(self, *, risolve: bool = True, regressione: bool = False) -> None:
        self.risolve = risolve
        self.regressione = regressione
        self.registri = 0
        self.passate_registro = 0
        self.interventi: list[str] = []
        self.piani: list[str] = []

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = schema.get("properties", {})
        if "locked_in_at_end" in props:
            self.registri += 1
            utente = messages[-1]["content"]
            prima_scena = "(è la prima scena del capitolo)" in utente
            if prima_scena:
                self.passate_registro += 1
            problemi = []
            if "Il cassetto" in utente and self.passate_registro == 1:
                problemi = [{"severity": "bloccante", "kind": "tempo",
                             "problem": "Nicolò è al Cairo alle 21:40 e in archivio alle 22:00",
                             "quote": "lascia il Cairo alle 21:40", "fix": "mostra il viaggio"}]
            if self.regressione and self.passate_registro >= 2 and "Il volo" in utente:
                problemi = [{"severity": "importante", "kind": "oggetto",
                             "problem": "la carta d'imbarco cambia nome a metà scena",
                             "quote": "carta d'imbarco", "fix": "un nome solo"}]
            custode = "Nicolò" if prima_scena else "la direttrice"
            return {"flashback": False, "day": "giovedì", "time_start": "", "time_end": "",
                    "place_start": "Cairo" if prima_scena else "archivio",
                    "place_end": "Cairo" if prima_scena else "archivio",
                    "arrival": "" if prima_scena else "atterra e prende un taxi",
                    "present": ["Nicolò"], "events": [],
                    "objects": [{"id": "fascicolo", "what": "il fascicolo", "holder": custode,
                                 "status": "con_se"}],
                    "access": [], "locked_in_at_end": False, "deadlines": [], "dates": [],
                    "season": "", "knowledge": [], "issues": problemi}
        if "threads" in props and "anticipations" in props:
            return {"threads": [], "anticipations": []}
        if "scenes" in props and "duplicates" in schema["properties"]["scenes"]["items"][
                "properties"]:
            return {"scenes": [
                {"section": 1, "wants": "partire", "obstacle": "i controlli", "changes": "parte",
                 "new": "il volo", "why_here": "ponte", "static": False, "duplicates": 0,
                 "quote": "lascia il Cairo"},
                {"section": 2, "wants": "vedere", "obstacle": "nessuno", "changes": "vede",
                 "new": "le fiale", "why_here": "prova", "static": False, "duplicates": 0,
                 "quote": "apre il cassetto"},
                {"section": 3, "wants": "vedere", "obstacle": "nessuno", "changes": "vede",
                 "new": "le fiale", "why_here": "—", "static": False, "duplicates": 2,
                 "quote": "il direttore apre il cassetto"}]}
        if set(props) == {"paragraphs"}:
            return {"paragraphs": [{"n": 2, "kind": "commento", "idea": "il sistema archivia",
                                    "image": "", "unused_detail": False}]}
        if "clusters" in props:
            return {"clusters": [{"label": "il sistema archivia", "kind": "ridondanza",
                                  "why": "la stessa tesi"}]}
        if "items" in props and "key" in props["items"]["items"]["properties"]:
            chiavi = props["items"]["items"]["properties"]["key"]["enum"]
            voto = 7.5 if self.interventi else 6.0
            fuori = {"items": [{"key": k, "score": voto, "verdict": "ok", "evidence": "una frase",
                                "problems": []} for k in chiavi]}
            if "authors" in props:
                fuori["authors"] = []
            return fuori
        if "changes" in props and "preserve" in props:
            self.piani.append(messages[-1]["content"])
            ids = re.findall(r"CH02-[A-Z]+-\d{3}", messages[-1]["content"])
            tempo = next(i for i in ids if "-TIME-" in i)
            scena = next(i for i in ids if "-SCENE-" in i)
            return {"changes": [
                {"issue_ids": [scena], "operation": "merge", "targets": [2, 3],
                 "first_paragraph": 0, "last_paragraph": 0,
                 "intended_effect": "una scena sola, con una prova nuova",
                 "protected_content": "le fiale", "words": 40},
                {"issue_ids": [tempo], "operation": "insert", "targets": [1],
                 "first_paragraph": 0, "last_paragraph": 1,
                 "intended_effect": "il viaggio mostrato", "protected_content": "", "words": 20}],
                "preserve": ["le fiale nel cassetto"], "author_questions": []}
        if "results" in props:
            ids = re.findall(r"- (CH02-[A-Z]+-\d{3})", messages[-1]["content"])
            return {"results": [{"issue_id": i, "status": "RESOLVED" if self.risolve else
                                 "UNRESOLVED", "evidence": "atterra a Parigi e prende un taxi"
                                 if self.risolve else ""} for i in ids]}
        if "items" in props and "kept" in props["items"]["items"]["properties"]:
            return {"items": [{"item": "le fiale nel cassetto", "kept": True,
                               "evidence": "gli mostra le fiale"}]}
        return {}

    def chat(self, messages, **kw):
        utente = messages[-1]["content"]
        self.interventi.append(utente)
        if "Fondi queste" in utente:
            return ("## Il cassetto\n\nLa direttrice apre il cassetto: le fiale sono sei, una è "
                    "vuota. Nicolò capisce che qualcuno è arrivato prima di lui, e che la direttrice lo sa "
                    "ma non lo dirà finché lui resta nella stanza.")
        return ("All'alba atterra a Parigi e prende un taxi fino all'archivio, con il fascicolo "
                "stretto sotto il braccio.")


def _libro(tmp_path: Path, modello) -> Forge:
    f = Forge(tmp_path / "book", AppConfig(), client=modello)
    f.start()
    f.state.notebook["form"] = "thriller storico"
    f.state.outline = [{"number": 1, "title": "Il Cairo", "words": 60},
                       {"number": 2, "title": "La porta", "words": 60}]
    f.project.chapter_path(1, "Il Cairo").write_text(
        "# Capitolo 1: Il Cairo\n\n## Il tetto\n\nNicolò scappa sui tetti del Cairo.\n",
        encoding="utf-8")
    f.project.chapter_path(2, "La porta").write_text(BOZZA, encoding="utf-8")
    f.save()
    return f


# --------------------------------------------------------------------------- #
#  i pezzi
# --------------------------------------------------------------------------- #
def test_an_object_changing_hands_without_a_transfer_is_an_issue() -> None:
    records = [tl.record_from_dict({"objects": [{"id": "pacco", "what": "il pacchetto di cera",
                                                 "holder": "Salma", "status": "con_se"}]}),
               tl.record_from_dict({"objects": [{"id": "pacco", "what": "il pacchetto di cera",
                                                 "holder": "Nicolò", "status": "con_se"}]})]
    registro = rv.object_registry(records)
    assert registro[0].line().startswith("il pacchetto di cera: [1] Salma")
    problemi = rv.object_issues(registro)
    assert problemi and "senza un passaggio di mano" in problemi[0][2]
    records[1].objects[0]["status"] = "consegnato"
    assert not rv.object_issues(rv.object_registry(records)), "un passaggio mostrato va bene"


def test_issues_get_stable_ids_and_duplicates_merge() -> None:
    ids = rv._Ids(11)
    grezzi = [rv.Issue(category="TIME", severity="Major", section=1,
                       diagnosis="il volo parte alle 21:40 e alle 21:15"),
              rv.Issue(category="TIME", severity="Blocker", section=1,
                       diagnosis="il volo parte alle 21:15 e alle 21:40", evidence="21:15"),
              rv.Issue(category="REP", severity="Moderate", section=2, diagnosis="filing")]
    rilievi = rv._consolidate(grezzi, ids)
    assert [r.issue_id for r in rilievi] == ["CH11-REP-001", "CH11-TIME-001"]
    assert next(r for r in rilievi if r.category == "TIME").severity == "Blocker"


def test_a_blocker_cannot_be_averaged_away() -> None:
    run = rv.RevisionRun(issues=[rv.Issue(issue_id="X-TIME-001", category="TIME",
                                          severity="Blocker", status="UNRESOLVED")])
    run.gates = rv.gates(run, tl.Timeline(), [], True)
    assert next(g for g in run.gates if g.key == "A").passed is False
    assert rv.verdict(run)[0] == "REVISE"
    run.author_questions = ["Salma tiene il pacchetto?"]
    assert rv.verdict(run)[0] == "AUTHOR INPUT REQUIRED"
    run.issues[0].status = "RESOLVED"
    run.author_questions = []
    run.gates = rv.gates(run, tl.Timeline(), [], True)
    assert rv.verdict(run)[0] == "PASS"
    run.issues.append(rv.Issue(issue_id="X-OBJ-001", category="OBJ", severity="Major",
                               status="REGRESSION"))
    run.gates = rv.gates(run, tl.Timeline(), [], True)
    assert rv.verdict(run)[0] == "FAIL"


def test_a_problem_the_timeline_still_finds_is_not_resolved() -> None:
    rilievo = rv.Issue(issue_id="X-TIME-001", category="TIME", severity="Blocker",
                       diagnosis="Nicolò è al Cairo alle 21:40 e in archivio alle 22:00",
                       origin="registro (modello)", status="RESOLVED")
    dopo = tl.Timeline(issues=[tl.Issue(tl.BLOCKING, "tempo",
                                        "Nicolò è al Cairo alle 21:40 e in archivio alle 22:00")])
    nuovi = rv.cross_check([rilievo], dopo)
    assert rilievo.status == "UNRESOLVED" and not nuovi
    nuovo = tl.Timeline(issues=[tl.Issue(tl.IMPORTANT, "oggetto", "la carta cambia nome")])
    assert rv.cross_check([rilievo], nuovo), "un problema nuovo è una regressione"


# --------------------------------------------------------------------------- #
#  il giro intero
# --------------------------------------------------------------------------- #
def test_a_full_revision_run_is_traceable(tmp_path: Path) -> None:
    modello = Revisore()
    f = _libro(tmp_path, modello)
    run = f.revise_chapter(2, apply=True)
    cartella = rv.run_dir(f.project, run.run_id)
    # 0. la baseline intatta e le fonti dell'app consultate davvero
    assert (cartella / "baseline.md").read_text(encoding="utf-8") == BOZZA
    precedente = next(s for s in run.sources_consulted if s["name"].startswith("capitolo prec"))
    assert precedente["path"].endswith("01_il-cairo.md") and precedente["version"]
    assert "originale dell'app" in precedente["status"]
    # 1. rilievi con ID, gravità, prova, test
    ids = [i.issue_id for i in run.issues]
    assert all(re.fullmatch(r"CH02-[A-Z]+-\d{3}", x) for x in ids)
    tempo = next(i for i in run.issues if i.category == "TIME")
    assert tempo.severity == "Blocker" and tempo.acceptance_test and tempo.reader_impact
    assert any(i.category == "SCENE" and "ripete la funzione" in i.diagnosis for i in run.issues)
    assert any(i.category == "OBJ" for i in run.issues), "il fascicolo cambia custode"
    # 2. il piano, nell'ordine dei blocchi (la cronologia prima delle scene)
    operazioni = [(c.operation, c.block) for c in run.changes]
    assert operazioni[0] == ("insert", "cronologia") and ("merge", "scene") in operazioni
    # il Major sugli oggetti che il piano non copriva riceve una riscrittura mirata, al suo posto
    oggetto = next(i for i in run.issues if i.category == "OBJ")
    assert ("rewrite", "oggetti") in operazioni and any(
        oggetto.issue_id in c.related_issue_ids for c in run.changes)
    assert operazioni.index(("rewrite", "oggetti")) < operazioni.index(("merge", "scene"))
    assert run.preserve == ["le fiale nel cassetto"]
    # 3. gli interventi: la fusione ha tolto una scena, il viaggio è inserito
    rivisto = Path(run.output_path).read_text(encoding="utf-8")
    assert rivisto.count("## ") == 2 and "atterra a Parigi" in rivisto
    assert all(c.done for c in run.changes if c.operation in ("insert", "merge"))
    # 4. la verifica: stati con prova, gate, verdetto
    assert tempo.status == "RESOLVED" and "atterra" in tempo.resolution_evidence
    assert [g.key for g in run.gates] == list("ABCDEFGHI")
    assert run.verdict in rv.VERDICTS
    # il report in dodici sezioni, il diff, i dati
    report = (cartella / "report.md").read_text(encoding="utf-8")
    for n in range(1, 13):
        assert f"## {n}. " in report
    assert "Fonti canoniche dell'app consultate" in report
    assert (cartella / "diff.md").exists() and rv.load_run(f.project, run.run_id).issues
    # il capitolo cambia solo se il verdetto lo permette, e la versione di prima resta
    if run.applied_to_chapter:
        versioni = ql.versions(f.project, "02_la-porta")
        assert versioni[0].read_text(encoding="utf-8") == BOZZA


def test_a_trial_revision_writes_elsewhere_and_never_touches_the_chapter(tmp_path: Path) -> None:
    f = _libro(tmp_path, Revisore())
    uscita = tmp_path / "prove" / "02_v4.md"
    run = f.revise_chapter(2, text=BOZZA, output=uscita, trial=True)
    assert uscita.exists() and run.output_path == str(uscita)
    assert f.project.chapter_path(2, "La porta").read_text(encoding="utf-8") == BOZZA
    assert not run.applied_to_chapter


def test_a_regression_fails_the_run_and_the_chapter_stays(tmp_path: Path) -> None:
    f = _libro(tmp_path, Revisore(regressione=True))
    run = f.revise_chapter(2, apply=True)
    assert any(i.status == "REGRESSION" for i in run.issues)
    assert run.verdict == "FAIL" and not run.applied_to_chapter
    assert f.project.chapter_path(2, "La porta").read_text(encoding="utf-8") == BOZZA
    assert "carta d'imbarco cambia nome" in (rv.run_dir(f.project, run.run_id) /
                                            "report.md").read_text(encoding="utf-8")


def test_improving_a_novel_runs_revision_rounds(tmp_path: Path) -> None:
    f = _libro(tmp_path, Revisore())
    resoconto = f.improve_chapter_quality(2, rounds=1)
    assert resoconto.rounds and "report: revisioni/ch02_" in resoconto.rounds[0]
    assert rv.latest_run(f.project, 2) is not None


# --------------------------------------------------------------------------- #
#  le lezioni della revisione v4: un intervento per scena, nessun taglio drastico
# --------------------------------------------------------------------------- #
def test_many_interventions_on_one_scene_become_one() -> None:
    """V4: «The Filing» riscritta sette volte di fila, sempre più corta."""
    sezioni = [("## A", "uno " * 100), ("## B", "due " * 100), ("## C", "tre " * 100)]
    interventi = [rv.Change(related_issue_ids=[f"X-{i}"], operation=op, targets=[1],
                            intended_effect=f"effetto {i}", block="prosa")
                  for i, op in enumerate(("rewrite", "trim", "insert", "rewrite"))]
    interventi.append(rv.Change(related_issue_ids=["X-9"], operation="merge", targets=[1, 2],
                                block="scene"))
    fuori = rv._one_per_scene(interventi, sezioni)
    singole = [c for c in fuori if c.targets == [1]]
    assert len(singole) == 1 and singole[0].operation == "rewrite"
    assert singole[0].related_issue_ids == ["X-0", "X-1", "X-2", "X-3"]
    assert singole[0].words == 100 and "effetto 3" in singole[0].intended_effect
    assert any(c.operation == "merge" for c in fuori), "le fusioni restano"


def test_a_trim_that_guts_a_scene_is_refused(tmp_path: Path) -> None:
    class Sventra(Revisore):
        def chat(self, messages, **kw):
            return "Nicolò esce."

    testo = "# Capitolo 2: X\n\n## Scena\n\n" + "\n\n".join(
        " ".join(["parola"] * 100) for _ in range(5))
    taglio = rv.Change(change_id="CHG-001", operation="trim", targets=[0], block="ripetizioni")
    rivisto = rv.execute(Sventra(), testo, [taglio], [], records=[], registro=[], start=[],
                         canon="", preserve=[], language="italiano", style="", kind="romanzo",
                         model=None, budget=500)
    assert rivisto.count("parola") == 500 and "scartato" in taglio.before_after_summary


def test_repetition_issues_are_capped_and_single_words_are_only_signals() -> None:
    ids = rv._Ids(11)
    grezzi = [rv.Issue(category="REP", severity="Major", section=k, diagnosis=f"tesi {k} " * 3)
              for k in range(8)]
    rilievi = rv._consolidate(grezzi, ids)
    assert len([r for r in rilievi if r.category == "REP"]) == rv.MAX_REP
    assert any("gruppi di ripetizione" in r.diagnosis for r in rilievi)
