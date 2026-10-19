"""Test del motore di riscrittura deterministico (client fittizio)."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.db.project_schema import (
    Chunk,
    Document,
    RewriteChunk,
    RewriteRun,
    init_project_db,
    make_engine,
)
from bookwriter.rewrite.engine import assemble_markdown, preview_pairs, rewrite_document


class FakeRewriteClient:
    def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
        user = messages[-1]["content"]
        return {
            "rewritten": f"RW[{user[:15]}]",
            "glossary": [{"term": "Luca", "rendering": "Luke"}],
            "summary": "riassunto progressivo",
        }


def _seed_book(db_path: Path) -> int:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        doc = Document(path="b.txt", format="txt", sha256="d", title="Libro")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        specs = [(0, "Cap 1", "Primo brano."), (1, "Cap 1", "Secondo brano."), (2, "Cap 2", "Terzo brano.")]
        for pos, ch, text in specs:
            ses.add(Chunk(document_id=doc.id, book_pos=pos, chapter_ref=ch, text=text, sha256=f"c{pos}"))
        ses.commit()
        return doc.id


def test_full_rewrite(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    report = rewrite_document(db, doc_id, "CONTRATTO", FakeRewriteClient())
    assert report.total == 3 and report.processed == 3 and report.status == "done"

    md = assemble_markdown(db, report.run_id)
    assert "# Libro" in md  # titolo del documento
    assert md.count("RW[") == 3  # tutti i chunk riscritti presenti in ordine
    # Continuità persistita (glossario).
    engine = make_engine(db)
    with Session(engine) as ses:
        run = ses.get(RewriteRun, report.run_id)
    assert run.continuity["glossary"]["Luca"] == "Luke"


def test_translate_mode(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    report = rewrite_document(
        db, doc_id, "C", FakeRewriteClient(), mode="translate", target_language="inglese"
    )
    assert report.status == "done"
    engine = make_engine(db)
    with Session(engine) as ses:
        run = ses.get(RewriteRun, report.run_id)
    assert run.mode == "translate" and run.target_language == "inglese"


def test_preview_only_first_chapter(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    report = rewrite_document(db, doc_id, "C", FakeRewriteClient(), kind="preview", resume=False)
    assert report.total == 2  # solo i due brani del Cap 1
    pairs = preview_pairs(db, report.run_id)
    assert len(pairs) == 2
    assert pairs[0]["original"] == "Primo brano." and pairs[0]["rewritten"].startswith("RW[")


def test_resume_skips_done_chunks(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    engine = make_engine(db)
    # Simula una run interrotta: run 'running' con il primo chunk già fatto.
    with Session(engine) as ses:
        chunks = list(ses.exec(select(Chunk).order_by(Chunk.book_pos)))
        run = RewriteRun(document_id=doc_id, kind="full", mode="rewrite", status="running", continuity={})
        ses.add(run)
        ses.commit()
        ses.refresh(run)
        ses.add(RewriteChunk(run_id=run.id, chunk_id=chunks[0].id, book_pos=0, chapter_ref="Cap 1", text="GIÀ"))
        ses.commit()
        run_id = run.id

    report = rewrite_document(db, doc_id, "C", FakeRewriteClient(), resume=True)
    assert report.run_id == run_id  # ha ripreso la stessa run
    assert report.processed == 3 and report.status == "done"
    with Session(engine) as ses:
        rows = list(ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == run_id)))
    assert len(rows) == 3  # nessun duplicato del primo chunk
    assert any(r.text == "GIÀ" for r in rows)  # quello già fatto è rimasto


def test_critic_can_be_turned_off_to_halve_the_calls(tmp_path: Path) -> None:
    """Il controllo di stile raddoppia abbondantemente le chiamate: dev'essere disattivabile."""

    class CountingClient(FakeRewriteClient):
        def __init__(self) -> None:
            super().__init__()
            self.chiamate = 0
            self.sforzi: list[str] = []

        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            self.chiamate += 1
            self.sforzi.append(reasoning_effort)
            return super().chat_json(messages, schema=schema, model=model)

    db = tmp_path / "p.db"
    doc_id = _seed_book(db)

    con = CountingClient()
    rewrite_document(db, doc_id, "contratto", con, kind="full", critic=True)

    db2 = tmp_path / "p2.db"
    doc2 = _seed_book(db2)
    senza = CountingClient()
    rewrite_document(db2, doc2, "contratto", senza, kind="full", critic=False)

    assert senza.chiamate < con.chiamate
    # Il controllo di stile è un giudizio: ragiona per intero, non a sforzo ridotto.
    assert all(sforzo == "" for sforzo in con.sforzi)


def test_pages_without_prose_are_copied_without_asking_the_model(tmp_path: Path) -> None:
    """L'indice di un PDF non ha nulla da tradurre: mandarlo al modello lo fa ripetere all'infinito."""

    class CountingClient(FakeRewriteClient):
        def __init__(self) -> None:
            self.chiamate = 0

        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            self.chiamate += 1
            return super().chat_json(messages, schema=schema, model=model)

    db = tmp_path / "p.db"
    init_project_db(db)
    engine = make_engine(db)
    indice = "Capitolo 1 .  .  .  .  .  .  .  12\nCapitolo 2 .  .  .  .  .  .  .  34\n"
    with Session(engine) as ses:
        doc = Document(path="b.pdf", format="pdf", sha256="d", title="Libro")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        ses.add(Chunk(document_id=doc.id, book_pos=0, chapter_ref=None, text=indice, sha256="c0"))
        ses.add(Chunk(document_id=doc.id, book_pos=1, chapter_ref="Cap 1", text="Prosa vera.", sha256="c1"))
        ses.commit()
        doc_id = doc.id

    client = CountingClient()
    report = rewrite_document(db, doc_id, "C", client, critic=False)

    assert client.chiamate == 1  # solo la prosa è finita al modello
    assert report.copied == 1
    with Session(engine) as ses:
        rows = {r.book_pos: r.text for r in ses.exec(select(RewriteChunk))}
    assert rows[0] == indice  # riportato invariato, non perso
    assert rows[1].startswith("RW[")


def test_a_stalled_model_does_not_throw_away_the_finished_chunks(tmp_path: Path) -> None:
    """Se il modello si blocca a ripetere su un passaggio, il resto del libro va salvato lo stesso."""
    from bookwriter.llm.client import LLMDegenerate

    class StallingClient(FakeRewriteClient):
        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            if "Secondo" in messages[-1]["content"]:
                raise LLMDegenerate("ripetizione senza fine")
            return super().chat_json(messages, schema=schema, model=model)

    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    report = rewrite_document(db, doc_id, "C", StallingClient(), critic=False)

    assert report.stalled == 1 and report.status == "done"
    engine = make_engine(db)
    with Session(engine) as ses:
        rows = {r.book_pos: r.text for r in ses.exec(select(RewriteChunk))}
    assert rows[1] == "Secondo brano."  # originale conservato al posto del buco
    assert rows[0].startswith("RW[") and rows[2].startswith("RW[")


def test_the_critic_is_shown_the_original_text(tmp_path: Path) -> None:
    """Senza l'originale il giudice non può valutare la fedeltà, e boccia qualunque cosa."""
    visti: list[str] = []

    class SpyingClient(FakeRewriteClient):
        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            if "conforms" in schema["properties"]:
                visti.append(messages[-1]["content"])
                return {"conforms": True, "issues": []}
            return super().chat_json(messages, schema=schema, model=model)

    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    rewrite_document(db, doc_id, "CONTRATTO", SpyingClient(), critic=True)

    assert visti, "il critic non è stato interpellato"
    assert "Primo brano." in visti[0] and "CONTRATTO" in visti[0]


def test_a_correction_is_adopted_only_if_it_passes_the_same_check(tmp_path: Path) -> None:
    """Riscrivere su rilievi mai verificati è peggio che non controllare: consegna l'ignoto."""

    class NeverSatisfiedClient(FakeRewriteClient):
        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            if "conforms" in schema["properties"]:
                return {"conforms": False, "issues": ["troppe metafore"]}
            if "Correggi questi scostamenti" in messages[0]["content"]:
                return {"rewritten": "VERSIONE-CORRETTA", "glossary": [], "summary": "s"}
            return super().chat_json(messages, schema=schema, model=model)

    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    rewrite_document(db, doc_id, "C", NeverSatisfiedClient(), critic=True)

    engine = make_engine(db)
    with Session(engine) as ses:
        testi = [r.text for r in ses.exec(select(RewriteChunk))]
    assert all(t.startswith("RW[") for t in testi)  # la correzione bocciata non entra nel libro


def test_a_silent_critic_does_not_destroy_the_translation(tmp_path: Path) -> None:
    """Il controllo di stile è un di più: se non risponde, il lavoro va consegnato lo stesso.

    Caso reale: un giudizio su 2.928 token si è bloccato a ripetere la stessa sequenza, e
    faceva fallire l'intera traduzione già completata.
    """
    from bookwriter.llm.client import LLMDegenerate

    class MuteCritic(FakeRewriteClient):
        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            if "conforms" in schema["properties"]:
                raise LLMDegenerate("si è bloccato a ripetere sempre la stessa sequenza")
            return super().chat_json(messages, schema=schema, model=model)

    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    report = rewrite_document(db, doc_id, "C", MuteCritic(), critic=True)

    assert report.status == "done" and report.processed == 3
    assert report.unchecked == 3
    assert "senza controllo di stile" in report.summary()
    engine = make_engine(db)
    with Session(engine) as ses:
        testi = [r.text for r in ses.exec(select(RewriteChunk))]
    assert all(t.startswith("RW[") for t in testi)  # le traduzioni ci sono tutte


# --------------------------------------------------------------------------- #
#  il libro tradotto torna nella sua impaginazione
# --------------------------------------------------------------------------- #
def _seed_impaginato(db_path: Path) -> int:
    """Un libro importato come lo importa il programma: layout, testo, chunk con le posizioni."""
    from bookwriter.ingest.chunker import chunk_document
    from bookwriter.ingest.structure import parse_structure
    from bookwriter.layout.model import BLANK, HEADING, PAGEBREAK, PARAGRAPH, DocumentLayout, Segment

    layout = DocumentLayout(
        segments=[
            Segment(kind=HEADING, text="Il diario", level=1, ref="p0"),
            Segment(kind=HEADING, text="Capitolo primo", level=2, ref="p1"),
            Segment(kind=PARAGRAPH, text="Era una notte buia.", ref="p2"),
            Segment(kind=PAGEBREAK),
            Segment(kind=BLANK),
            Segment(kind=PARAGRAPH, text="Il giorno dopo pioveva.", ref="p3"),
        ],
        source_format="txt",
        title="Il diario",
    )
    testo = layout.to_text()
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        doc = Document(
            path="b.txt", format="txt", sha256="d", title="Il diario",
            char_count=len(testo), meta={"layout": layout.to_dict()},
        )
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        for c in chunk_document(testo, parse_structure(testo), target_tokens=12):
            ses.add(
                Chunk(
                    document_id=doc.id, book_pos=c.book_pos, chapter_ref=c.chapter_ref,
                    span_start=c.span_start, span_end=c.span_end, text=c.text,
                    token_count=c.token_count, sha256=f"c{c.book_pos}",
                )
            )
        ses.commit()
        return doc.id


class TraduttoreOrdinato:
    """Fa quel che gli si chiede: traduce e restituisce gli stessi paragrafi."""

    def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
        if "conforms" in schema["properties"]:
            return {"conforms": True, "issues": []}
        import re

        tradotti = []
        for paragrafo in messages[-1]["content"].split("\n\n"):
            paragrafo = paragrafo.strip()
            if not paragrafo:
                continue
            # Come da istruzione: i cancelletti del titolo restano dove sono.
            marca = re.match(r"^(#{1,6} )", paragrafo)
            testa = marca.group(1) if marca else ""
            tradotti.append(f"{testa}EN({paragrafo[len(testa):]})")
        return {"rewritten": "\n\n".join(tradotti), "glossary": [], "summary": "s"}


def test_the_translation_is_poured_back_into_the_original_layout(tmp_path: Path) -> None:
    """Cambia la lingua dentro ai paragrafi; titoli, stacchi e pagine bianche restano dov'erano."""
    from bookwriter.rewrite.engine import assemble_layout

    db = tmp_path / "p.db"
    doc_id = _seed_impaginato(db)
    report = rewrite_document(
        db, doc_id, "C", TraduttoreOrdinato(), mode="translate", target_language="inglese"
    )
    montato = assemble_layout(db, report.run_id)

    assert montato is not None and montato.misaligned == 0
    assert [s.kind for s in montato.layout.segments] == [
        "heading", "heading", "paragraph", "pagebreak", "blank", "paragraph"
    ]
    # I titoli sono tradotti ma restano titoli, coi loro livelli e i loro riferimenti.
    titoli = [s for s in montato.layout.segments if s.kind == "heading"]
    assert [s.level for s in titoli] == [1, 2]
    assert [s.ref for s in titoli] == ["p0", "p1"]
    assert titoli[0].text == "EN(Il diario)", "i cancelletti non entrano nel titolo del libro"
    assert all(s.text.startswith("EN(") for s in montato.layout.segments if s.has_text)


def test_a_model_that_merges_the_paragraphs_does_not_lose_a_word(tmp_path: Path) -> None:
    """Se il modello ignora la richiesta e consegna un blocco unico, il testo va salvato lo stesso."""
    from bookwriter.rewrite.engine import assemble_layout

    class TraduttoreDistratto(TraduttoreOrdinato):
        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            fuori = super().chat_json(messages, schema=schema, model=model)
            if "rewritten" in fuori:
                fuori["rewritten"] = fuori["rewritten"].replace("\n\n", " ")  # tutto attaccato
            return fuori

    db = tmp_path / "p.db"
    doc_id = _seed_impaginato(db)
    report = rewrite_document(db, doc_id, "C", TraduttoreDistratto())
    montato = assemble_layout(db, report.run_id)

    assert montato is not None
    intero = " ".join(s.text for s in montato.layout.segments if s.has_text)
    for pezzo in ("Il diario", "Capitolo primo", "Era una notte buia.", "Il giorno dopo pioveva."):
        assert f"EN({pezzo})" in intero, "nessun passaggio è andato perso"
    assert montato.misaligned > 0 and "ricuciti" in montato.note()


def test_the_book_is_written_in_the_format_that_was_asked_for(tmp_path: Path) -> None:
    """La scelta del formato vale per la traduzione come per tutto il resto."""
    from bookwriter.rewrite.engine import write_result

    db = tmp_path / "p.db"
    doc_id = _seed_impaginato(db)
    report = rewrite_document(db, doc_id, "C", TraduttoreOrdinato())
    for fmt in ("md", "docx", "pdf"):
        out, _nota = write_result(db, report.run_id, tmp_path / f"libro.{fmt}", fmt)
        assert out.exists() and out.stat().st_size > 0
    markdown = (tmp_path / "libro.md").read_text(encoding="utf-8")
    assert markdown.startswith("# EN(Il diario)")
    assert "## EN(Capitolo primo)" in markdown
    assert "---" in markdown, "lo stacco di pagina è arrivato fino in fondo"


def test_an_old_document_without_a_layout_is_still_exported(tmp_path: Path) -> None:
    """Chi ha importato prima non resta senza libro: si ricade sulla vecchia resa."""
    from bookwriter.rewrite.engine import write_result

    db = tmp_path / "p.db"
    doc_id = _seed_book(db)  # nessun layout in meta
    report = rewrite_document(db, doc_id, "C", FakeRewriteClient())
    out, nota = write_result(db, report.run_id, tmp_path / "vecchio.md", "md")

    assert out.exists() and "RW[" in out.read_text(encoding="utf-8")
    assert "reimportalo" in nota, "e l'utente viene avvisato del perché"


def test_a_run_can_be_stopped_and_picks_up_where_it_left_off(tmp_path: Path) -> None:
    """Una traduzione dura ore: si deve poter fermare senza perdere quello che ha già fatto."""
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    fatti = {"n": 0}

    class Contatore(FakeRewriteClient):
        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            fatti["n"] += 1
            return super().chat_json(messages, schema=schema, model=model)

    cliente = Contatore()
    primo = rewrite_document(
        db, doc_id, "C", cliente, critic=False, should_stop=lambda: fatti["n"] >= 1
    )

    assert primo.stopped and primo.status == "running"
    assert primo.processed == 1, "un brano tradotto, gli altri nemmeno tentati"
    assert "interrotto" in primo.summary() and "riprende" in primo.summary()

    secondo = rewrite_document(db, doc_id, "C", cliente, critic=False)

    assert secondo.run_id == primo.run_id, "ha ripreso la stessa lavorazione"
    assert secondo.status == "done" and secondo.processed == 3
    assert fatti["n"] == 3, "i due mancanti, non tutti e tre da capo"
    with Session(make_engine(db)) as ses:
        righe = list(ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == primo.run_id)))
    assert len(righe) == 3 and not any(r.text == "" for r in righe)


def test_stopping_before_the_first_passage_leaves_the_book_untouched(tmp_path: Path) -> None:
    """Fermarsi subito non deve produrre una lavorazione a metà che sembra completa."""
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    report = rewrite_document(db, doc_id, "C", FakeRewriteClient(), should_stop=lambda: True)

    assert report.stopped and report.processed == 0 and report.status == "running"
    with Session(make_engine(db)) as ses:
        assert list(ses.exec(select(RewriteChunk))) == []


def test_the_same_book_in_two_languages_are_two_separate_works(tmp_path: Path) -> None:
    """Cinque lingue sono cinque lavori, non uno solo scritto da cinque mani.

    Senza questo vincolo la ripresa cercava la lavorazione per documento e modalità soltanto:
    la traduzione francese riprendeva quella inglese, ne saltava i brani già fatti e scriveva i
    propri nello stesso mucchio. Il risultato era un unico file in lingue mescolate.
    """
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)

    class Traduttore(FakeRewriteClient):
        def __init__(self, sigla: str) -> None:
            self.sigla = sigla

        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            return {"rewritten": f"{self.sigla}[{messages[-1]['content'][:12]}]",
                    "glossary": [], "summary": "s"}

    inglese = rewrite_document(db, doc_id, "C", Traduttore("EN"), mode="translate",
                               target_language="inglese", critic=False)
    francese = rewrite_document(db, doc_id, "C", Traduttore("FR"), mode="translate",
                                target_language="francese", critic=False)

    assert inglese.run_id != francese.run_id, "due lingue, due lavorazioni"
    assert francese.processed == 3, "il francese traduce tutti i brani, non solo quelli avanzati"
    with Session(make_engine(db)) as ses:
        en = [r.text for r in ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == inglese.run_id))]
        fr = [r.text for r in ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == francese.run_id))]
    assert len(en) == len(fr) == 3
    assert all(t.startswith("EN[") for t in en), "nell'inglese solo inglese"
    assert all(t.startswith("FR[") for t in fr), "nel francese solo francese"


def test_two_styles_on_the_same_book_do_not_mix_either(tmp_path: Path) -> None:
    """Stesso ragionamento per la riscrittura: due stili sono due lavori distinti."""
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)

    primo = rewrite_document(db, doc_id, "stile A", FakeRewriteClient(),
                             style_profile_id=1, critic=False)
    secondo = rewrite_document(db, doc_id, "stile B", FakeRewriteClient(),
                               style_profile_id=2, critic=False)

    assert primo.run_id != secondo.run_id
    assert secondo.processed == 3, "il secondo stile riscrive tutto il libro"


def test_resuming_the_same_language_still_picks_up_where_it_left(tmp_path: Path) -> None:
    """Il vincolo sulla lingua non deve rompere la ripresa, che è il motivo per cui esiste."""
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    fatti = {"n": 0}

    class Contatore(FakeRewriteClient):
        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            fatti["n"] += 1
            return super().chat_json(messages, schema=schema, model=model)

    cliente = Contatore()
    primo = rewrite_document(db, doc_id, "C", cliente, mode="translate", target_language="inglese",
                             critic=False, should_stop=lambda: fatti["n"] >= 1)
    secondo = rewrite_document(db, doc_id, "C", cliente, mode="translate",
                               target_language="inglese", critic=False)

    assert secondo.run_id == primo.run_id, "stessa lingua, stessa lavorazione ripresa"
    assert fatti["n"] == 3, "i due mancanti, non tutti e tre da capo"


def test_a_blocked_passage_is_retried_split_in_two(tmp_path: Path) -> None:
    """Un brano che manda il modello in ripetizione spesso passa se lo si divide.

    Il blocco nasce dalla struttura del passaggio — un elenco di domande, una tabella — e le due
    metà separate non ce l'hanno più. Senza questo tentativo quel brano resterebbe nella lingua
    di partenza in mezzo al libro tradotto.
    """
    from bookwriter.llm.client import LLMDegenerate

    class SiBloccaSuiLunghi(FakeRewriteClient):
        """Va in ripetizione sul testo intero, ma se lo riceve a metà traduce."""

        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            testo = messages[-1]["content"]
            if "PRIMA" in testo and "SECONDA" in testo:
                raise LLMDegenerate("si è bloccato a ripetere")
            return {"rewritten": f"EN[{testo[:14]}]", "glossary": [], "summary": "s"}

    db = tmp_path / "p.db"
    init_project_db(db)
    engine = make_engine(db)
    with Session(engine) as ses:
        doc = Document(path="b.md", format="md", sha256="d", title="Libro")
        ses.add(doc); ses.commit(); ses.refresh(doc)
        ses.add(Chunk(document_id=doc.id, book_pos=0, sha256="c0",
                      text="PRIMA parte con abbastanza testo da contare.\n\n"
                           "SECONDA parte, altrettanto lunga e piena di parole."))
        ses.commit()
        doc_id = doc.id

    report = rewrite_document(db, doc_id, "C", SiBloccaSuiLunghi(), mode="translate",
                              target_language="inglese", critic=False)

    assert report.salvaged == 1 and report.stalled == 0
    assert "recuperati dividendoli" in report.summary()
    with Session(engine) as ses:
        testo = ses.exec(select(RewriteChunk)).first().text
    assert testo.startswith("EN[") and "PRIMA parte con abbastanza" not in testo


def test_a_passage_left_in_the_source_language_can_be_redone(tmp_path: Path) -> None:
    """Un libro consegnato con un brano non tradotto si corregge senza rifarlo tutto."""
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)

    class SiBloccaSulSecondo(FakeRewriteClient):
        def __init__(self) -> None:
            self.arreso = False

        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            from bookwriter.llm.client import LLMDegenerate

            if "Secondo" in messages[-1]["content"] and not self.arreso:
                raise LLMDegenerate("bloccato")
            return super().chat_json(messages, schema=schema, model=model)

    cliente = SiBloccaSulSecondo()
    primo = rewrite_document(db, doc_id, "C", cliente, critic=False)
    assert primo.stalled == 1, "il brano è rimasto nella lingua di partenza"
    with Session(make_engine(db)) as ses:
        rimasti = [r.text for r in ses.exec(select(RewriteChunk)) if r.text == "Secondo brano."]
    assert len(rimasti) == 1

    # Il modello adesso risponde: si rifà solo quel brano, non il libro.
    cliente.arreso = True
    secondo = rewrite_document(db, doc_id, "C", cliente, critic=False, redo_stalled=True)

    assert secondo.run_id == primo.run_id, "stessa lavorazione, non una nuova"
    assert secondo.stalled == 0
    with Session(make_engine(db)) as ses:
        testi = [r.text for r in ses.exec(select(RewriteChunk))]
    assert len(testi) == 3 and all(t.startswith("RW[") for t in testi)


def test_progress_counts_the_whole_book_not_only_what_is_left(tmp_path: Path) -> None:
    """Riprendendo a metà, «0 su 72» farebbe credere che il libro sia lungo 72 e vergine.

    Il numero che l'utente legge dev'essere quello del libro: 25 su 97, non 0 su 72.
    """
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)  # tre brani
    fatti = {"n": 0}

    class Contatore(FakeRewriteClient):
        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            fatti["n"] += 1
            return super().chat_json(messages, schema=schema, model=model)

    cliente = Contatore()
    rewrite_document(db, doc_id, "C", cliente, critic=False, should_stop=lambda: fatti["n"] >= 1)

    passi: list[tuple[int, int]] = []
    rewrite_document(db, doc_id, "C", cliente, critic=False,
                     progress=lambda f, t: passi.append((f, t)))

    assert passi[0] == (1, 3), "riparte da un brano già fatto su tre, non da zero su due"
    assert passi[-1] == (3, 3)
    assert all(totale == 3 for _f, totale in passi), "il totale è sempre quello del libro"
