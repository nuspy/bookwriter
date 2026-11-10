"""Test della coda: più libri in fila, uno dopo l'altro, e la possibilità di fermarli."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from bookwriter.batch import (
    ANNULLATO,
    ATTESA,
    FALLITO,
    FATTO,
    INTERROTTO,
    IN_CORSO,
    QueuedBook,
    run_queue,
)
from bookwriter.db.project_schema import Chunk, Document, init_project_db, make_engine
from bookwriter.llm.client import LLMError


class Cliente:
    """Traduce sempre, e conta quante volte l'hanno chiamato."""

    def __init__(self) -> None:
        self.chiamate = 0

    def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
        if "conforms" in schema["properties"]:
            return {"conforms": True, "issues": []}
        self.chiamate += 1
        return {"rewritten": f"EN[{messages[-1]['content'][:20]}]", "glossary": [], "summary": "s"}


def _libro(db_path: Path, titolo: str, brani: int = 3) -> int:
    init_project_db(db_path)
    with Session(make_engine(db_path)) as ses:
        doc = Document(path=f"{titolo}.txt", format="txt", sha256=titolo, title=titolo)
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        for pos in range(brani):
            ses.add(Chunk(
                document_id=doc.id, book_pos=pos, chapter_ref="Cap 1",
                text=f"Brano numero {pos} di {titolo}, abbastanza lungo da contare come prosa.",
                sha256=f"{titolo}{pos}",
            ))
        ses.commit()
        return doc.id


def _coda(tmp_path: Path, db: Path, quanti: int = 3) -> list[QueuedBook]:
    return [
        QueuedBook(
            document_id=_libro(db, f"libro{n}"),
            title=f"libro{n}",
            out_path=tmp_path / f"uscita{n}.md",
            fmt="md",
            mode="translate",
            target_language="inglese",
            critic=False,
        )
        for n in range(quanti)
    ]


def test_the_books_are_worked_one_after_the_other(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    coda = _coda(tmp_path, db)

    report = run_queue(coda, db, Cliente())

    assert report.done == 3 and report.failed == 0
    assert all(b.state == FATTO for b in coda)
    for n in range(3):
        assert (tmp_path / f"uscita{n}.md").exists()
    assert "3/3 libri completati" in report.summary()


def test_each_book_goes_where_it_was_told(tmp_path: Path) -> None:
    """Nome e cartella sono di ciascun libro: è il senso di metterli in fila da soli."""
    db = tmp_path / "p.db"
    altrove = tmp_path / "consegne" / "inglese"
    coda = _coda(tmp_path, db, quanti=2)
    coda[1].out_path = altrove / "Il mio romanzo tradotto.md"

    run_queue(coda, db, Cliente())

    assert (tmp_path / "uscita0.md").exists()
    assert (altrove / "Il mio romanzo tradotto.md").exists(), "cartella creata e nome rispettato"


def test_a_book_that_fails_does_not_stop_the_queue(tmp_path: Path) -> None:
    """La coda esiste per tornare e trovare fatto tutto quello che si poteva fare."""
    db = tmp_path / "p.db"
    coda = _coda(tmp_path, db)

    class ClienteCapriccioso(Cliente):
        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            if "libro1" in messages[-1]["content"]:
                raise LLMError("il modello non risponde")
            return super().chat_json(messages, schema=schema, model=model)

    report = run_queue(coda, db, ClienteCapriccioso())

    assert [b.state for b in coda] == [FATTO, FALLITO, FATTO]
    assert "non risponde" in coda[1].detail
    assert report.done == 2 and report.failed == 1
    assert "1 non riusciti" in report.summary()
    assert (tmp_path / "uscita2.md").exists(), "il terzo è stato fatto lo stesso"


def test_the_queue_stops_between_books_and_finishes_the_one_in_hand(tmp_path: Path) -> None:
    """«Ferma dopo il libro in corso»: il file di quello in lavorazione va scritto comunque."""
    db = tmp_path / "p.db"
    coda = _coda(tmp_path, db)
    fermata = {"chiesta": False}

    def basta() -> bool:
        return fermata["chiesta"]

    def dopo_il_primo(_indice, libro) -> None:
        if libro.state == FATTO:
            fermata["chiesta"] = True

    report = run_queue(coda, db, Cliente(), should_stop=basta, on_change=dopo_il_primo)

    assert coda[0].state == FATTO and (tmp_path / "uscita0.md").exists()
    assert [b.state for b in coda[1:]] == [ANNULLATO, ANNULLATO]
    assert report.stopped and "coda fermata" in report.summary()
    assert not (tmp_path / "uscita1.md").exists()


def test_interrupting_stops_inside_the_book_and_keeps_what_was_done(tmp_path: Path) -> None:
    """«Interrompi»: si smette subito, senza consegnare un libro a metà — ma senza buttarlo."""
    from bookwriter.rewrite import rewrite_document

    db = tmp_path / "p.db"
    coda = _coda(tmp_path, db, quanti=2)
    cliente = Cliente()

    def dopo_due_brani() -> bool:
        return cliente.chiamate >= 2

    report = run_queue(coda, db, cliente, should_abort=dopo_due_brani)

    assert coda[0].state == INTERROTTO
    assert not (tmp_path / "uscita0.md").exists(), "nessun file monco da confondere col finito"
    assert "riparte da qui" in coda[0].detail
    assert coda[1].state == ANNULLATO
    assert "interrotti a metà" in report.summary()

    # E quello che era stato tradotto è ancora lì: la ripresa non lo rifà.
    prima = cliente.chiamate
    ripresa = rewrite_document(
        db, coda[0].document_id, "", cliente, mode="translate",
        target_language="inglese", critic=False,
        literary=False,  # si contano le sole traduzioni, non i passaggi letterari accessori
    )
    assert ripresa.status == "done"
    assert cliente.chiamate - prima < 3, "ha ripreso da dove era, non da capo"


def test_a_book_already_done_is_not_worked_twice(tmp_path: Path) -> None:
    """Riavviare la coda dopo un blocco non deve rifare i libri già consegnati."""
    db = tmp_path / "p.db"
    coda = _coda(tmp_path, db, quanti=2)
    coda[0].state = FATTO
    cliente = Cliente()

    run_queue(coda, db, cliente)

    assert not (tmp_path / "uscita0.md").exists(), "il primo era già fatto: non lo si rifà"
    assert (tmp_path / "uscita1.md").exists()


def test_the_queue_says_what_it_is_doing_while_it_does_it(tmp_path: Path) -> None:
    """Chi guarda la coda deve vedere la fila avanzare, non una barra ferma per ore."""
    db = tmp_path / "p.db"
    coda = _coda(tmp_path, db, quanti=2)
    passaggi: list[tuple[int, int, int, int]] = []
    stati: list[str] = []

    dettagli: list[str] = []
    run_queue(
        coda, db, Cliente(),
        progress=lambda i, n, f, tot: passaggi.append((i, n, f, tot)),
        on_change=lambda _i, libro: (stati.append(libro.state), dettagli.append(libro.detail)),
    )

    assert stati.count(FATTO) == 2, "entrambi i libri arrivano in fondo"
    assert passaggi, "l'avanzamento dentro al libro viene riportato"
    assert passaggi[-1][:2] == (2, 2), "e alla fine la coda risulta completa"
    # A che punto è il libro dev'essere leggibile mentre lavora, non solo alla fine: la barra
    # della coda si muove di un punto ogni molti minuti e sembra bloccata.
    assert "1/3 brani" in dettagli and "3/3 brani" in dettagli


def test_the_operation_is_named_the_way_a_person_would_name_it() -> None:
    """La colonna «Operazione» la legge una persona, non il motore."""
    assert QueuedBook(1, "x", Path("a.md"), mode="translate", target_language="inglese").operation \
        == "traduci in inglese"
    assert QueuedBook(1, "x", Path("a.md"), mode="rewrite", style_name="Calvino").operation \
        == "riscrivi nello stile «Calvino»"
    assert QueuedBook(1, "x", Path("a.md")).state == ATTESA


# --------------------------------------------------------------------------- #
#  la coda sopravvive alla chiusura del programma
# --------------------------------------------------------------------------- #
def test_the_queue_is_still_there_after_closing_the_program(tmp_path: Path) -> None:
    """Una fila di lavori da ore va ritrovata la mattina, non ricomposta a mano."""
    from bookwriter.batch import load_queue, save_queue

    db = tmp_path / "p.db"
    coda = _coda(tmp_path, db, quanti=2)
    coda[1].mode = "rewrite"
    coda[1].style_name = "Calvino"
    coda[1].contract = "Adotta in modo uniforme il seguente stile."
    coda[1].ratio = 0.7
    coda[1].critic = False
    coda[1].state = FATTO
    coda[1].detail = "run #9: elaborati 3/3"

    save_queue(db, coda)
    riletta = load_queue(db)  # come alla riapertura del programma

    assert [b.title for b in riletta] == ["libro0", "libro1"], "stesso ordine"
    secondo = riletta[1]
    assert secondo.mode == "rewrite" and secondo.style_name == "Calvino"
    assert secondo.contract.startswith("Adotta") and secondo.ratio == 0.7
    assert secondo.critic is False
    assert secondo.state == FATTO and secondo.detail == "run #9: elaborati 3/3"
    assert riletta[0].out_path == tmp_path / "uscita0.md" and riletta[0].fmt == "md"
    assert riletta[0].target_language == "inglese"


def test_saving_the_queue_replaces_it_instead_of_piling_up(tmp_path: Path) -> None:
    """Togliere un libro dalla coda e salvare non deve lasciarlo nel database."""
    from bookwriter.batch import load_queue, save_queue

    db = tmp_path / "p.db"
    coda = _coda(tmp_path, db, quanti=3)
    save_queue(db, coda)
    save_queue(db, [coda[2], coda[0]])  # tolto uno, gli altri due riordinati

    riletta = load_queue(db)
    assert [b.title for b in riletta] == ["libro2", "libro0"]


def test_a_queued_book_is_not_swept_away_on_reopening(tmp_path: Path) -> None:
    """Il libro di lavoro va cancellato alla riapertura, ma non se ha un lavoro in sospeso."""
    from bookwriter.batch import load_queue, save_queue
    from bookwriter.db.project_schema import list_documents, purge_transient_documents
    from bookwriter.ingest import pipeline

    db = tmp_path / "p.db"
    init_project_db(db)
    for nome in ("da_tradurre", "gia_fatto"):
        (tmp_path / f"{nome}.md").write_text(f"# {nome}\n\nTesto.\n", encoding="utf-8")
        pipeline.ingest_paths([tmp_path / f"{nome}.md"], db_path=db, transient=True)
    documenti = {d["title"]: d["id"] for d in list_documents(db)}
    save_queue(db, [
        QueuedBook(document_id=documenti["da_tradurre"], title="da_tradurre",
                   out_path=tmp_path / "a.md", state=ATTESA),
        QueuedBook(document_id=documenti["gia_fatto"], title="gia_fatto",
                   out_path=tmp_path / "b.md", state=FATTO),
    ])

    tolti = purge_transient_documents(db)  # riapertura del programma

    assert tolti == 1, "solo quello il cui lavoro è finito"
    assert [d["title"] for d in list_documents(db)] == ["da_tradurre"]
    # E la voce di coda del libro sparito se ne va con lui: sarebbe un lavoro impossibile.
    assert [b.title for b in load_queue(db)] == ["da_tradurre"]


# --------------------------------------------------------------------------- #
#  ordine di lavorazione: prima le lingue che costano meno
# --------------------------------------------------------------------------- #
def test_languages_are_grouped_by_what_they_cost_the_model() -> None:
    """Il gruppo dipende da quanto il tokenizzatore spezza la lingua, non dall'alfabeto."""
    from bookwriter.batch import language_cost_group

    for lingua in ("Inglese", "francese", "Tedesco", "Spagnolo", "Portoghese", "english"):
        assert language_cost_group(lingua) == 0, lingua
    for lingua in ("Ungherese", "Russo", "Ucraino", "magyar"):
        assert language_cost_group(lingua) == 1, lingua
    # Anche scritte a mano con qualche svista, come capita comporre una coda di ventiquattro voci.
    for lingua in ("Mandarino", "Giappanese", "Indi", "Ebraico moderno", "Arabico", "Thailandese"):
        assert language_cost_group(lingua) == 2, lingua
    assert language_cost_group(None) == 2, "senza lingua non si può promettere che sia veloce"


def test_the_queue_is_reordered_without_touching_what_is_done(tmp_path: Path) -> None:
    """Riordinare non deve rimettere in lavorazione un libro già consegnato."""
    from bookwriter.batch import sort_queue_by_language_cost

    coda = [
        QueuedBook(1, "a", Path("a.md"), target_language="Inglese", state=FATTO),
        QueuedBook(2, "b", Path("b.md"), target_language="Giappanese"),
        QueuedBook(3, "c", Path("c.md"), target_language="Ungherese"),
        QueuedBook(4, "d", Path("d.md"), target_language="Francese"),
        QueuedBook(5, "e", Path("e.md"), target_language="Arabico"),
        QueuedBook(6, "f", Path("f.md"), target_language="Spagnolo"),
    ]
    ordinata = sort_queue_by_language_cost(coda)

    assert [b.target_language for b in ordinata] == [
        "Inglese",                          # già fatto: resta in testa e non si rilavora
        "Francese", "Spagnolo",             # gruppo veloce, nell'ordine in cui erano
        "Ungherese",                        # gruppo intermedio
        "Giappanese", "Arabico",            # il resto, nell'ordine in cui era
    ]
    assert ordinata[0].state == FATTO


def test_reordering_while_it_runs_takes_effect_immediately(tmp_path: Path) -> None:
    """Chi riordina la coda mentre lavora si aspetta che valga da subito, non alla prossima volta.

    Il prossimo libro va scelto a ogni giro dalla fila viva. Prendendone una copia all'avvio,
    il riordino non avrebbe effetto fino al riavvio del programma — e chi l'ha fatto non
    verrebbe avvisato: vedrebbe la nuova fila sullo schermo e la vecchia in lavorazione.
    """
    db = tmp_path / "p.db"
    coda = _coda(tmp_path, db, quanti=3)
    for n, libro in enumerate(coda):
        libro.title = f"libro{n}"
    ordine: list[str] = []

    def riordina_dopo_il_primo(_indice, libro) -> None:
        # La coda annuncia a ogni brano: qui interessa solo quando cambia il libro.
        if libro.state == IN_CORSO and (not ordine or ordine[-1] != libro.title):
            ordine.append(libro.title)
        if libro.state == FATTO and libro.title == "libro0":
            # Mentre la coda gira, l'ultimo passa davanti — sul posto, come fa la finestra.
            coda[:] = [coda[0], coda[2], coda[1]]

    run_queue(coda, db, Cliente(), on_change=riordina_dopo_il_primo)

    assert ordine == ["libro0", "libro2", "libro1"], "il nuovo ordine vale subito"
    assert all(b.state == FATTO for b in coda), "e nessun libro viene saltato"


def test_a_book_added_while_it_runs_is_picked_up(tmp_path: Path) -> None:
    """Accodare un libro mentre la coda lavora non deve richiedere di riavviarla."""
    db = tmp_path / "p.db"
    coda = _coda(tmp_path, db, quanti=1)
    aggiunto = QueuedBook(
        document_id=_libro(db, "tardivo"), title="tardivo",
        out_path=tmp_path / "tardivo.md", fmt="md", mode="translate",
        target_language="inglese", critic=False,
    )
    fatto = {"n": 0}

    def accoda_dopo_il_primo(_indice, libro) -> None:
        if libro.state == FATTO:
            fatto["n"] += 1
            if fatto["n"] == 1:
                coda.append(aggiunto)

    report = run_queue(coda, db, Cliente(), on_change=accoda_dopo_il_primo)

    assert report.done == 2, "anche quello aggiunto in corsa viene lavorato"
    assert (tmp_path / "tardivo.md").exists()
