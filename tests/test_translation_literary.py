"""Traduzione letteraria: modi di dire decisi dall'utente, stile dell'originale, rifinitura."""

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
from bookwriter.llm.client import LLMDegenerate, LLMError
from bookwriter.rewrite.engine import (
    answer_idioms,
    idiom_decisions,
    rewrite_document,
    run_record,
)
from bookwriter.rewrite.literary import (
    DECISA,
    NON_DECISA,
    chiave_idioma,
    interpreta_scelta,
    rifinisci,
)

LUPO = "in bocca al lupo"
OPZIONI_LUPO = [
    {"tipo": "equivalente", "resa": "break a leg", "spiegazione": "stesso augurio scaramantico"},
    {"tipo": "letterale", "resa": "into the wolf's mouth", "spiegazione": "oscuro per chi legge"},
    {"tipo": "esplicativa", "resa": "good luck", "spiegazione": "chiaro ma perde il colore"},
]


class Letterario:
    """Un modello finto che sa fare tutti i passaggi della traduzione letteraria.

    Riconosce il passaggio dallo schema chiesto e registra cosa gli è stato mandato.
    """

    def __init__(self, *, rifinitura=None, stile=True, idiomi_rotti=False) -> None:
        self.sistemi: list[str] = []  # system prompt delle chiamate di traduzione
        self.utenti: list[str] = []  # testi mandati da tradurre
        self.chiamate: dict[str, int] = {"voci": 0, "rifinito": 0, "dimensions": 0,
                                         "rewritten": 0, "conforms": 0}
        self.campioni: list[str] = []
        self.rifinitura = rifinitura  # funzione testo → testo rifinito (None: lo marca)
        self.stile = stile
        self.idiomi_rotti = idiomi_rotti

    def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
        props = schema["properties"]
        utente = messages[-1]["content"]
        if "voci" in props:
            self.chiamate["voci"] += 1
            if self.idiomi_rotti:
                raise LLMDegenerate("si è bloccato a ripetere")
            if "lupo" in utente:
                return {"voci": [{"originale": LUPO, "contesto": "Gli disse: in bocca al lupo.",
                                  "tipo": "modo di dire", "opzioni": OPZIONI_LUPO}]}
            return {"voci": []}
        if "rifinito" in props:
            self.chiamate["rifinito"] += 1
            tradotto = utente.split("TRADUZIONE:\n", 1)[1]
            if self.rifinitura is None:
                return {"rifinito": tradotto.replace("EN(", "EN~(")}
            return {"rifinito": self.rifinitura(tradotto)}
        if "dimensions" in props:
            self.chiamate["dimensions"] += 1
            self.campioni.append(utente)
            if not self.stile:
                return {"dimensions": {}, "exemplars": [], "constraints": []}
            dims = {d: "" for d in ("sintassi", "punteggiatura", "lessico", "figure", "ritmo",
                                    "voce", "tono", "coesione", "paragrafo")}
            dims.update(ritmo="frasi brevi e secche", tono="ironico e asciutto")
            return {"dimensions": dims, "exemplars": ["Pioveva. Lui no."], "constraints": []}
        if "conforms" in props:
            self.chiamate["conforms"] += 1
            return {"conforms": True, "issues": []}
        self.chiamate["rewritten"] += 1
        self.sistemi.append(messages[0]["content"])
        self.utenti.append(utente)
        resa = "\n\n".join(f"EN({p.strip()})" for p in utente.split("\n\n") if p.strip())
        return {"rewritten": resa, "glossary": [{"term": "Luca", "rendering": "Luke"}],
                "summary": "s"}


def _libro(db_path: Path, testi: list[str]) -> int:
    init_project_db(db_path)
    with Session(make_engine(db_path)) as ses:
        doc = Document(path="b.txt", format="txt", sha256="d", title="Libro")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        for pos, testo in enumerate(testi):
            ses.add(Chunk(document_id=doc.id, book_pos=pos, chapter_ref="Cap 1", text=testo,
                          sha256=f"c{pos}"))
        ses.commit()
        return doc.id


TRE_BRANI = [
    "Luca partì all'alba. Gli disse: in bocca al lupo.",
    "La strada era lunga e polverosa, e nessuno parlava.",
    "Al ritorno la madre gli ripeté: in bocca al lupo, figlio mio.",
]


def _testi(db: Path, run_id: int) -> dict[int, str]:
    with Session(make_engine(db)) as ses:
        return {r.book_pos: r.text for r in ses.exec(
            select(RewriteChunk).where(RewriteChunk.run_id == run_id))}


# --------------------------------------------------------------------------- #
#  modi di dire e riferimenti culturali: decide l'utente
# --------------------------------------------------------------------------- #
def test_an_idiom_is_put_to_the_user_with_its_options_and_the_choice_binds_the_translator(
    tmp_path: Path,
) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    domande: list[dict] = []

    def decidi(voce: dict):
        domande.append(voce)
        return 0  # l'equivalente nella cultura di arrivo

    client = Letterario()
    report = rewrite_document(db, doc_id, "C", client, mode="translate",
                              target_language="inglese", critic=False, decide_idiom=decidi)

    assert len(domande) == 1, "lo stesso modo di dire non si chiede due volte nello stesso libro"
    voce = domande[0]
    assert voce["originale"] == LUPO and "in bocca al lupo" in voce["contesto"]
    assert len(voce["opzioni"]) == 3 and all(o["spiegazione"] for o in voce["opzioni"])
    assert voce["lingua"] == "inglese" and voce["book_pos"] == 0
    # La scelta è vincolante nei due brani che contengono l'espressione, non nell'altro.
    con_scelta = [s for s in client.sistemi if "«in bocca al lupo» → «break a leg»" in s]
    assert len(con_scelta) == 2
    assert report.idioms_asked == 1 and report.idioms_pending == 0
    assert "sottoposti alla tua scelta" in report.summary()

    registro = run_record(db, report.run_id)
    voce_reg = registro["idiomi"][chiave_idioma(LUPO)]
    assert voce_reg["stato"] == DECISA and voce_reg["resa"] == "break a leg"
    assert voce_reg["tipo_scelta"] == "equivalente" and len(voce_reg["brani"]) == 2


def test_without_an_answer_nothing_is_applied_and_the_idiom_stays_undecided(tmp_path: Path) -> None:
    """Nessuno a cui chiedere (la coda): si traduce come si tradurrebbe, e si segna «non decisa»."""
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    client = Letterario()
    report = rewrite_document(db, doc_id, "C", client, mode="translate",
                              target_language="inglese", critic=False)

    assert not any("Rese decise dall'utente" in s for s in client.sistemi)
    assert report.idioms_pending == 1 and "in attesa di una tua decisione" in report.summary()
    pendenti = idiom_decisions(db, report.run_id, pending_only=True)
    assert [v["originale"] for v in pendenti] == [LUPO]
    assert pendenti[0]["stato"] == NON_DECISA and pendenti[0]["chiesta"] is False


def test_a_late_answer_reopens_only_the_passages_with_that_idiom(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    client = Letterario()
    primo = rewrite_document(db, doc_id, "C", client, mode="translate",
                             target_language="inglese", critic=False)
    chiave = chiave_idioma(LUPO)

    riaperti = answer_idioms(db, primo.run_id, {chiave: "good luck, son"})

    assert riaperti == 2, "i due brani con l'espressione, non quello senza"
    with Session(make_engine(db)) as ses:
        assert ses.get(RewriteRun, primo.run_id).status == "running"
    assert sorted(_testi(db, primo.run_id)) == [1]

    client.sistemi.clear()
    secondo = rewrite_document(db, doc_id, "C", client, mode="translate",
                               target_language="inglese", critic=False)
    assert secondo.run_id == primo.run_id and secondo.status == "done"
    assert len(client.sistemi) == 2, "si ritraducono solo i due brani riaperti"
    assert all("«good luck, son»" in s for s in client.sistemi)
    voce = run_record(db, primo.run_id)["idiomi"][chiave]
    assert voce["stato"] == DECISA and voce["tipo_scelta"] == "personalizzata"
    # Rispondere di nuovo con la stessa resa non riapre niente.
    assert answer_idioms(db, primo.run_id, {chiave: "good luck, son"}) == 0


def test_decisions_taken_in_the_preview_are_not_asked_again_for_the_whole_book(
    tmp_path: Path,
) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    domande: list[dict] = []

    def decidi(voce):
        domande.append(voce)
        return 2

    rewrite_document(db, doc_id, "C", Letterario(), mode="translate", target_language="inglese",
                     critic=False, kind="preview", resume=False, decide_idiom=decidi)
    assert len(domande) == 1
    client = Letterario()
    rewrite_document(db, doc_id, "C", client, mode="translate", target_language="inglese",
                     critic=False, decide_idiom=decidi)

    assert len(domande) == 1, "la decisione vale per il documento in quella lingua"
    assert any("«good luck»" in s for s in client.sistemi)
    # In un'altra lingua la decisione è un'altra: si chiede di nuovo.
    rewrite_document(db, doc_id, "C", Letterario(), mode="translate", target_language="francese",
                     critic=False, decide_idiom=decidi)
    assert len(domande) == 2


def test_a_failed_idiom_pass_or_a_broken_dialog_does_not_stop_the_translation(
    tmp_path: Path,
) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    report = rewrite_document(db, doc_id, "C", Letterario(idiomi_rotti=True), mode="translate",
                              target_language="inglese", critic=False)
    assert report.status == "done" and report.processed == 3
    assert len(run_record(db, report.run_id)["idiomi_errori"]) == 3

    db2 = tmp_path / "p2.db"
    doc2 = _libro(db2, TRE_BRANI)

    def finestra_rotta(_voce):
        raise RuntimeError("finestra chiusa")

    report = rewrite_document(db2, doc2, "C", Letterario(), mode="translate",
                              target_language="inglese", critic=False,
                              decide_idiom=finestra_rotta)
    assert report.status == "done" and report.idioms_pending == 1


def test_notes_appear_among_the_options_only_when_asked() -> None:
    from bookwriter.rewrite.literary import trova_idiomi

    class Spia:
        def __init__(self):
            self.sistema = ""

        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            self.sistema = messages[0]["content"]
            return {"voci": []}

    spia = Spia()
    trova_idiomi("testo", "inglese", spia, None)
    assert "nota del traduttore" not in spia.sistema and "senza note" in spia.sistema
    trova_idiomi("testo", "inglese", spia, None, note=True)
    assert "nota del traduttore" in spia.sistema


def test_the_answer_can_be_an_index_a_text_or_nothing() -> None:
    voce = {"opzioni": OPZIONI_LUPO}
    assert interpreta_scelta(voce, 1)["resa"] == "into the wolf's mouth"
    assert interpreta_scelta(voce, "break a leg")["opzione"] == 0
    assert interpreta_scelta(voce, "fingers crossed")["tipo_scelta"] == "personalizzata"
    assert interpreta_scelta(voce, None) is None
    assert interpreta_scelta(voce, 9) is None and interpreta_scelta(voce, "  ") is None


# --------------------------------------------------------------------------- #
#  lo stile dell'originale guida la traduzione
# --------------------------------------------------------------------------- #
def test_the_style_of_the_original_is_extracted_and_becomes_the_contract(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    client = Letterario()
    report = rewrite_document(db, doc_id, "Mantieni lo stile.", client, mode="translate",
                              target_language="inglese", critic=False, should_stop=None)

    assert client.chiamate["dimensions"] == 1
    # Il campione attraversa il libro, non si ferma al primo brano.
    assert "Luca partì" in client.campioni[0] and "Al ritorno" in client.campioni[0]
    assert all("frasi brevi e secche" in s and "estratto automaticamente" in s
               for s in client.sistemi)
    assert report.style_source == "estratto dall'originale"
    assert "stile: estratto dall'originale" in report.summary()
    stile = run_record(db, report.run_id)["stile"]
    assert stile["profilo"]["dimensions"]["tono"] == "ironico e asciutto"
    assert "frasi brevi e secche" in stile["contratto"]


def test_resuming_does_not_extract_the_style_again(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    client = Letterario()
    fatti = {"n": 0}

    def basta():
        fatti["n"] += 1
        return fatti["n"] > 2  # il primo controllo è prima di tutto, il secondo dopo un brano

    rewrite_document(db, doc_id, "C", client, mode="translate", target_language="inglese",
                     critic=False, should_stop=basta)
    rewrite_document(db, doc_id, "C", client, mode="translate", target_language="inglese",
                     critic=False)
    assert client.chiamate["dimensions"] == 1
    assert all("frasi brevi e secche" in s for s in client.sistemi), "stesso contratto dopo la ripresa"


def test_a_style_chosen_by_the_user_wins_over_the_extraction(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    client = Letterario()
    report = rewrite_document(db, doc_id, "STILE SCELTO", client, mode="translate",
                              target_language="inglese", critic=False, style_profile_id=7)

    assert client.chiamate["dimensions"] == 0
    assert all(s.startswith("STILE SCELTO") for s in client.sistemi)
    assert run_record(db, report.run_id)["stile"] == {"origine": "scelto dall'utente",
                                                     "profilo_id": 7}


def test_a_failed_extraction_falls_back_to_the_given_contract(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    client = Letterario(stile=False)
    report = rewrite_document(db, doc_id, "CONTRATTO DI RISERVA", client, mode="translate",
                              target_language="inglese", critic=False)
    assert report.status == "done"
    assert all(s.startswith("CONTRATTO DI RISERVA") for s in client.sistemi)
    assert report.style_source == "estrazione non riuscita"


# --------------------------------------------------------------------------- #
#  rifinitura madrelingua
# --------------------------------------------------------------------------- #
def test_the_native_editor_polishes_every_passage(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    client = Letterario()
    report = rewrite_document(db, doc_id, "C", client, mode="translate",
                              target_language="inglese", critic=False)

    assert client.chiamate["rifinito"] == 3
    assert all(t.startswith("EN~(") for t in _testi(db, report.run_id).values())
    assert report.polished == 3 and "3 brani rifiniti" in report.summary()
    assert set(map(tuple, run_record(db, report.run_id)["rifinitura"].values())) == {("applicata",)}


def test_the_polish_can_be_turned_off(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    client = Letterario()
    report = rewrite_document(db, doc_id, "C", client, mode="translate",
                              target_language="inglese", critic=False, polish=False)
    assert client.chiamate["rifinito"] == 0
    assert all(t.startswith("EN(") for t in _testi(db, report.run_id).values())


def test_a_polish_that_changes_the_length_too_much_is_thrown_away(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    client = Letterario(rifinitura=lambda t: t + " E poi aggiunse molte altre cose mai dette.")
    report = rewrite_document(db, doc_id, "C", client, mode="translate",
                              target_language="inglese", critic=False)
    assert all(t.startswith("EN(") and "mai dette" not in t
               for t in _testi(db, report.run_id).values())
    assert report.polished == 0 and report.polish_kept == 3
    esiti = run_record(db, report.run_id)["rifinitura"]
    assert all(e[0].startswith("scartata: lunghezza") for e in esiti.values())


def test_the_polish_guards() -> None:
    class Fisso:
        def __init__(self, risposta):
            self.risposta = risposta

        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            if isinstance(self.risposta, Exception):
                raise self.risposta
            return {"rifinito": self.risposta}

    tradotto = "Luke left at dawn, quiet.\n\nThe road was long and dusty."

    def prova(risposta, *, paragrafi=True, fissati=("Luke",)):
        return rifinisci(tradotto, originale="orig", target_language="inglese",
                         vincolati=list(fissati), client=Fisso(risposta), model=None,
                         conserva_paragrafi=paragrafi)

    buono = "Luke set off at dawn, silent.\n\nThe road was long and dusty."
    assert prova(buono) == (buono, "applicata")
    unito = buono.replace("\n\n", " ")
    assert prova(unito)[1].startswith("scartata: paragrafi")
    assert prova(unito, paragrafi=False)[0] == unito, "con la prosa i paragrafi non vincolano"
    senza_nome = buono.replace("Luke", "Lucas")
    assert prova(senza_nome)[1].startswith("scartata: cambiati termini fissati")
    assert prova("## Title\n\n" + buono)[1].startswith("scartata")
    assert prova("")[1] == "scartata: risposta vuota"
    testo, esito = prova(LLMError("muto"))
    assert testo == tradotto and esito.startswith("non riuscita")


def test_a_plain_rewrite_does_not_run_the_literary_passes(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _libro(db, TRE_BRANI)
    client = Letterario()
    report = rewrite_document(db, doc_id, "C", client, critic=False)
    assert client.chiamate["voci"] == client.chiamate["rifinito"] == 0
    assert client.chiamate["dimensions"] == 0 and report.style_source == ""
