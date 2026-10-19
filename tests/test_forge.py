"""Le sette fasi: dalla conversazione con l'autore al libro composto, con un modello fittizio.

Nessuna rete e nessun LM Studio: il modello è finto e risponde guardando lo schema che gli
viene chiesto. Quello che si verifica qui è il contratto delle fasi — in particolare la regola
zero, che un capitolo esiste solo se il file esiste.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from bookwriter.config import AppConfig, ResearchSettings
from bookwriter.forge import interview as iv
from bookwriter.forge import notebook as nb
from bookwriter.forge import production as prod
from bookwriter.forge import saves as sv
from bookwriter.forge import spec as sp
from bookwriter.forge import writing as wr
from bookwriter.forge.engine import Forge
from bookwriter.forge.project import BookProject, ForgeState, planned_chapters, status
from bookwriter.llm.client import LLMError

#: Un quaderno come lo lascerebbe una conversazione andata a fondo.
QUADERNO = {
    "title": "La marea di Trieste",
    "form": "romanzo giallo",
    "language": "it",
    "premise": "Una commissaria indaga su un annegamento che non è un incidente.",
    "author_goal": "Far sentire la città come un personaggio.",
    "messages": ["La verità costa, e non tutti possono pagarla."],
    "reader_effect": "Inquietudine, poi una pietà inattesa per il colpevole.",
    "audience": "Lettori adulti di gialli d'atmosfera.",
    "adaptation": "Capitoli brevi, lessico concreto, niente gergo poliziesco.",
    "length": "70.000 parole",
    "plot": "Il corpo in porto, le false piste, il segreto della famiglia Sartori.",
    "events": ["Il corpo nel porto", "La lettera anonima", "La confessione in cantiere"],
    "causality": ["La lettera arriva perché il fratello vuole essere scoperto."],
    "psychology": ["Elena diffida di chi le somiglia."],
    "key_scenes": ["La bora che spegne le luci del molo"],
    "ending": "Elena lascia andare il colpevole, e se ne pente.",
    "characters": [{"name": "Elena Sartori", "role": "commissaria", "appearance": "alta, capelli grigi",
                    "mind": "ostinata", "culture": "triestina, famiglia slovena",
                    "arc": "impara a fidarsi", "notes": ""}],
    "setting": "Trieste, inverno 1978.",
    "places": [{"name": "Molo Audace", "description": "battuto dalla bora"}],
    "style_notes": "",
    "avoid": ["scene di tortura"],
    "agreed": ["Il colpevole è il fratello"],
    "open_questions": [],
    "assistant_decisions": [],
}


# --------------------------------------------------------------------------- #
#  un modello che non c'è
# --------------------------------------------------------------------------- #
class FintoModello:
    """Risponde in modo plausibile, riconoscendo il compito dallo schema o dal prompt."""

    RISPOSTA = "Interessante. **Chi** trova il corpo, e perché proprio lui?"

    def __init__(self, *, chapters: int = 3, words: int = 900) -> None:
        self.chapters, self.words = chapters, words
        self.chiamate: list[str] = []
        self.conversazioni: list[list[dict]] = []
        self.quaderni: list[str] = []
        self.chiude = False  # l'autore, nell'ultimo tratto, ha chiesto di chiudere

    def chat(self, messages, *, model=None, temperature=0.0, max_tokens=None, extra=None,
             on_delta=None):
        sistema = messages[0]["content"]
        self.chiamate.append(sistema[:40])
        if "Sei un editor esperto" in sistema:
            self.conversazioni.append(messages)
            if on_delta is not None:
                for pezzo in (self.RISPOSTA[:13], self.RISPOSTA[13:]):
                    on_delta("content", pezzo)
            return self.RISPOSTA
        if "capitolo di libro" in sistema:
            utente = messages[-1]["content"]
            numero = utente.split("Capitolo ")[-1].split(":")[0].strip() if "Capitolo " in utente else "1"
            # frasi diverse: una prosa che ripete la stessa frase è proprio quello che il programma rifà
            corpo = " ".join(f"Una frase di prosa che dice qualcosa di preciso numero {i}." for i in range(108))
            return (f"# Capitolo {numero}: Titolo del capitolo\n\n## Apertura\n\n{corpo}\n\n"
                    "## Note bibliografiche\n\nRossi, M. (2024). *Un'opera*. Editore.")
        if "sezione di un libro" in sistema:
            return "# Introduzione\n\n" + " ".join(["Testo introduttivo."] * 40)
        return "Una risposta."

    def chat_json(self, messages, *, schema, model=None):
        props = set(schema.get("properties", {}))
        if "characters" in props:
            self.quaderni.append(messages[-1]["content"])
            if "ha chiuso la conversazione" in messages[0]["content"]:
                return {**QUADERNO, "open_questions": ["resta qualcosa?"],
                        "assistant_decisions": ["Il libro si apre con il corpo nel porto"],
                        "removed": []}
            aggiornamento = {**nb.empty(), "premise": QUADERNO["premise"],
                             "characters": QUADERNO["characters"],
                             "open_questions": ["Chi trova il corpo?"], "removed": []}
            if iv.CLOSE_KEY in props:  # solo l'aggiornamento chiede se l'autore vuole chiudere
                aggiornamento[iv.CLOSE_KEY] = self.chiude
            return aggiornamento
        if "chapters" in props:
            return {
                "parts": [{"number": 1, "title": "Le fondamenta"}],
                "chapters": [
                    {"number": i, "part": 1, "title": f"Il passaggio {i}",
                     "does": f"Stabilisce la cosa numero {i}.", "words": self.words,
                     "points": [f"Scena {k} del capitolo {i}" for k in (1, 2, 3)]}
                    for i in range(1, self.chapters + 1)
                ],
                "front_matter": ["introduzione"], "back_matter": ["bibliografia"],
                "rationale": "Tre passaggi, tre capitoli.",
            }
        if "works" in props:
            return {"works": [{"title": "Un libro che c'è", "author": "Bianchi", "year": "2023",
                               "url": "", "draw_from": "La scena del molo, la bora che spegne le "
                               "luci", "cited_sources": ["Archivio di Stato (1978)"],
                               "different": "Il tuo libro parte dal fratello"}],
                    "sources": ["Archivio di Stato (1978)"], "ideas": ["Una scena al molo"],
                    "recent": ""}
        return {"findings": [], "open_questions": []}

    def embed(self, texts, *, kind="document", model=None):
        return [[0.0] * 8 for _ in texts]


def _quaderno_pieno(state: ForgeState) -> None:
    """Una conversazione chiusa, con il quaderno completo."""
    state.notebook = nb.normalise(QUADERNO)
    state.interview_closed = True


@pytest.fixture()
def forge(tmp_path: Path) -> Forge:
    f = Forge(tmp_path / "book", AppConfig(), client=FintoModello())
    f.start()
    return f


# --------------------------------------------------------------------------- #
#  fase 1 — la conversazione
# --------------------------------------------------------------------------- #
def test_the_assistant_is_told_not_to_ask_about_print_or_style(forge: Forge) -> None:
    """Stampa e copertina si decidono a mano; lo stile viene dalla tendina: chiederli è rumore."""
    prompt = iv.system_prompt(forge.state, style="Adotta in modo UNIFORME lo stile noir.")
    for parola in ("margini", "copertina", "rilegatura", "misura della pagina"):
        assert parola in prompt
    assert "Non chiedere quale stile usare" in prompt
    assert "Adotta in modo UNIFORME lo stile noir." in prompt
    # E quello di cui deve occuparsi davvero.
    for parola in ("trama", "personaggi", "nessi causali", "meccanismi psicologici", "finale",
                   "lettore", "luoghi"):
        assert parola in prompt


def test_a_reply_arrives_in_pieces_and_is_saved(forge: Forge) -> None:
    pezzi: list[str] = []
    risposta = forge.talk("Un giallo a Trieste, d'inverno.",
                          on_delta=lambda tipo, p: pezzi.append(p) if tipo == "content" else None)
    assert risposta == FintoModello.RISPOSTA
    assert "".join(pezzi) == risposta
    ripreso = Forge(forge.project.root, AppConfig())
    ruoli = [b["role"] for b in ripreso.state.transcript]
    assert ruoli == ["assistant", "user", "assistant"], "l'apertura, l'autore, la risposta"
    assert ripreso.state.transcript[0]["text"] == iv.OPENING


def test_the_model_sees_a_conversation_that_starts_from_the_author(forge: Forge) -> None:
    """Molti modelli rifiutano due battute di fila dello stesso ruolo, o l'assistente per primo."""
    forge.talk("Prima idea.")
    forge.state.say("user", "Ci ho ripensato.")
    forge.talk("E poi un'altra cosa.")
    messaggi = forge.client.conversazioni[-1][1:]
    assert messaggi[0]["role"] == "user"
    assert all(a["role"] != b["role"] for a, b in zip(messaggi, messaggi[1:]))
    assert "Ci ho ripensato." in messaggi[-1]["content"]
    assert "E poi un'altra cosa." in messaggi[-1]["content"]


def test_what_the_author_wrote_survives_a_model_that_does_not_answer(tmp_path: Path) -> None:
    class Rotto(FintoModello):
        def chat(self, *a, **k):
            raise LLMError("nessuna risposta")

    f = Forge(tmp_path / "book", AppConfig(), client=Rotto())
    with pytest.raises(LLMError):
        f.talk("Il mio libro parla del mare.")
    ripreso = Forge(tmp_path / "book", AppConfig(), client=FintoModello())
    assert ripreso.state.transcript[-1] == {"role": "user", "text": "Il mio libro parla del mare."}
    # «Riprova» risponde a quella battuta, senza riscriverla.
    assert ripreso.talk(None) == FintoModello.RISPOSTA
    assert [b["role"] for b in ripreso.state.transcript][-2:] == ["user", "assistant"]


def test_a_stopped_reply_keeps_what_had_arrived(forge: Forge) -> None:
    """L'autore l'ha letto, e può volerci rispondere: quello che è arrivato resta."""
    def basta(tipo: str, pezzo: str) -> None:
        if pezzo != FintoModello.RISPOSTA[:13]:
            raise iv.Stopped()

    with pytest.raises(iv.Stopped):
        forge.talk("Un giallo.", on_delta=basta)
    assert forge.state.transcript[-1]["text"] == FintoModello.RISPOSTA[:13].strip() + " …"


def test_the_notebook_follows_the_conversation(forge: Forge) -> None:
    forge.talk("Un giallo a Trieste.")
    assert forge.update_notebook().read
    assert forge.state.notebook["premise"] == QUADERNO["premise"]
    assert forge.state.notebook["characters"][0]["name"] == "Elena Sartori"
    assert forge.state.notebook_upto == len(forge.state.transcript)
    # Il tratto successivo comincia dalla battuta dell'assistente a cui l'autore risponde.
    forge.talk("La trova un pescatore.")
    forge.update_notebook()
    ultimo = forge.client.quaderni[-1]
    assert "Un giallo a Trieste." not in ultimo
    assert FintoModello.RISPOSTA in ultimo and "La trova un pescatore." in ultimo


def test_the_assistant_is_told_it_cannot_start_anything(forge: Forge) -> None:
    """Il difetto vero: «Mi metto al lavoro…» detto da una chat che non può avviare niente."""
    prompt = iv.system_prompt(forge.state)
    assert "non puoi avviare, chiudere, scrivere né salvare niente" in prompt
    assert "Non dire mai che «ti metti al lavoro»" in prompt
    assert "«Ho finito: decidi tu il resto»" in prompt


def test_the_update_reports_a_close_request_without_saving_it(forge: Forge) -> None:
    forge.talk("Puoi iniziare il lavoro, grazie.")
    forge.client.chiude = True
    esito = forge.update_notebook()
    assert esito.read and esito.close_requested
    assert iv.CLOSE_KEY not in forge.state.notebook
    assert iv.CLOSE_KEY not in nb.normalise(forge.state.notebook)
    ripreso = Forge(forge.project.root, AppConfig())
    assert iv.CLOSE_KEY not in ripreso.state.notebook
    assert not forge.state.interview_closed, "riportarla non chiude: decide chi chiama"


def test_the_update_does_not_report_a_close_request_nobody_made(forge: Forge) -> None:
    forge.talk("Un giallo a Trieste.")
    esito = forge.update_notebook()
    assert esito.read and not esito.close_requested
    # Solo un vero «sì»: un valore storto del modello non chiude il libro.
    forge.talk("Altro.")
    forge.client.chiude = "true"
    assert not forge.update_notebook().close_requested


def test_the_close_field_is_only_in_the_update_schema() -> None:
    aggiornamento, finale = iv.update_schema(), nb.schema()
    assert iv.CLOSE_KEY not in finale["properties"] and iv.CLOSE_KEY not in finale["required"]
    assert aggiornamento["properties"][iv.CLOSE_KEY]["type"] == "boolean"
    assert aggiornamento["additionalProperties"] is False
    assert set(aggiornamento["required"]) == set(aggiornamento["properties"])
    assert set(aggiornamento["properties"]) - set(finale["properties"]) == {iv.CLOSE_KEY}


def test_the_final_pass_does_not_ask_for_the_close_field(forge: Forge) -> None:
    visti: list[dict] = []
    originale = forge.client.chat_json

    def spia(messages, *, schema, **kw):
        visti.append(schema)
        return originale(messages, schema=schema, **kw)

    forge.client.chat_json = spia
    forge.talk("Un giallo a Trieste.")
    forge.finish_interview()
    assert visti and all(iv.CLOSE_KEY not in sch["properties"] for sch in visti)


def test_a_notebook_update_that_fails_is_caught_up_later(forge: Forge) -> None:
    forge.talk("Un giallo a Trieste.")
    originale = forge.client.chat_json
    forge.client.chat_json = lambda *a, **k: (_ for _ in ()).throw(LLMError("giù"))
    with pytest.raises(LLMError):
        forge.update_notebook()
    forge.client.chat_json = originale
    forge.talk("La trova un pescatore.")
    forge.update_notebook()
    ultimo = forge.client.quaderni[-1]
    assert "Un giallo a Trieste." in ultimo and "La trova un pescatore." in ultimo


def test_the_notebook_does_not_lose_what_the_model_forgets_to_repeat() -> None:
    prima = nb.normalise(QUADERNO)
    dopo = nb.merge(prima, {"premise": "", "characters": [], "events": [],
                            "open_questions": [], "ending": "Un altro finale."})
    assert dopo["premise"] == QUADERNO["premise"], "una voce piena non si svuota per distrazione"
    assert [p["name"] for p in dopo["characters"]] == ["Elena Sartori"]
    assert dopo["events"] == QUADERNO["events"]
    assert dopo["ending"] == "Un altro finale.", "un valore nuovo prende il posto del vecchio"
    tolto = nb.merge(prima, {"characters": []}, removed=["Elena Sartori"])
    assert tolto["characters"] == [], "sparisce solo quello che l'autore ha tolto"
    risolte = nb.merge({**prima, "open_questions": ["Chi la trova?"]}, {"open_questions": []})
    assert risolte["open_questions"] == [], "una questione risolta esce dall'elenco"


def test_a_character_detail_is_not_lost_when_the_model_rewrites_the_character() -> None:
    prima = nb.normalise(QUADERNO)
    dopo = nb.merge(prima, {"characters": [{"name": "elena sartori", "role": "commissaria",
                                            "appearance": "", "mind": "ostinata e ironica",
                                            "culture": "", "arc": "", "notes": ""}]})
    elena = dopo["characters"][0]
    assert elena["mind"] == "ostinata e ironica"
    assert elena["appearance"] == "alta, capelli grigi"


def test_finishing_lets_the_assistant_decide_the_rest(forge: Forge) -> None:
    assert forge.requirements("interview"), "finché l'autore non chiude, la fase è aperta"
    forge.talk("Un giallo a Trieste.")
    messaggio = forge.finish_interview()
    assert forge.state.interview_closed
    assert forge.requirements("interview") == []
    assert forge.state.notebook["open_questions"] == [], "le questioni aperte le scioglie lui"
    assert "Il libro si apre con il corpo nel porto" in messaggio
    assert forge.state.transcript[-1]["text"] == messaggio
    assert forge.phases()[0].state == "fatta"


def test_closing_twice_announces_only_the_new_decisions(forge: Forge) -> None:
    """Il modello rimanda indietro il quaderno intero, decisioni di ieri comprese."""
    forge.talk("Un giallo a Trieste.")
    forge.finish_interview()
    forge.talk("Cambio idea sul finale.")
    originale = forge.client.chat_json

    def con_una_nuova(messages, **kw):
        risultato = originale(messages, **kw)
        if "ha chiuso la conversazione" in messages[0]["content"]:
            risultato["assistant_decisions"] = ["Il libro si apre con il corpo nel porto",
                                                "Il finale resta aperto"]
        return risultato

    forge.client.chat_json = con_una_nuova
    messaggio = forge.finish_interview()
    assert "Il finale resta aperto" in messaggio
    assert "corpo nel porto" not in messaggio
    assert forge.state.notebook["assistant_decisions"] == [
        "Il libro si apre con il corpo nel porto", "Il finale resta aperto"]


@pytest.mark.parametrize("codice", ["es", "ro", "sl", "en", "hu"])
def test_an_iso_code_is_never_mistaken_for_another_language(codice: str) -> None:
    """«es» sta dentro «inglese»: cercato fra i nomi, un libro spagnolo usciva in inglese."""
    assert nb.language_code(ForgeState(notebook={"language": codice})) == codice


def test_the_book_language_is_a_code_wherever_it_comes_from() -> None:
    assert nb.language_code(ForgeState(notebook={"language": "en"})) == "en"
    assert nb.language_code(ForgeState(answers={"libro.lingua": "inglese"})) == "en"
    assert nb.language_code(ForgeState(language="fr")) == "fr"
    assert nb.language_code(ForgeState()) == "it"


def test_a_book_begun_with_the_old_questions_keeps_what_it_knew() -> None:
    """Un libro a metà stesura non deve perdere il brief perché l'intervista è cambiata."""
    vecchio = ForgeState(answers={"libro.tesi": "Il mare ha una memoria",
                                  "oggetto.taglio": "A5 · 148×210 mm"})
    testo = nb.brief(vecchio)
    assert "Il mare ha una memoria" in testo
    assert "148×210" not in testo, "le vecchie risposte sulla stampa non dicono come scrivere"


# --------------------------------------------------------------------------- #
#  fase 2 — specifica
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("testo", "atteso"),
    [
        ("55.000 parole ≈ 190 pagine", 55000),
        ("circa 40000 parole", 40000),
        ("200 pagine", 200),
        ("non lo so", None),
    ],
)
def test_the_length_is_read_from_a_conversational_answer(testo: str, atteso: int | None) -> None:
    assert sp.parse_words(testo) == atteso


def test_the_word_budget_adds_up_to_what_the_author_asked(forge: Forge) -> None:
    """Il modello sbaglia a sommare: il totale lo decide l'autore, non lui."""
    _quaderno_pieno(forge.state)
    forge.state.notebook["length"] = "30.000 parole"
    outline = sp.propose_outline(forge.state, forge.client, chapters=3)
    somma = sum(c["words"] for c in outline["chapters"])
    assert abs(somma - 30_000) <= 30_000 * 0.06, somma
    assert [c["number"] for c in outline["chapters"]] == [1, 2, 3]


def test_the_specification_is_written_to_disk_and_can_be_read_back(forge: Forge) -> None:
    _quaderno_pieno(forge.state)
    testo = forge.make_spec()
    assert forge.project.spec_path.exists()
    assert forge.project.spec_path.read_text(encoding="utf-8") == testo
    previsti = planned_chapters(forge.project.spec_path)
    assert previsti == {i: f"Il passaggio {i}" for i in (1, 2, 3)}


def test_the_specification_carries_the_notebook_and_no_print_decisions(forge: Forge) -> None:
    _quaderno_pieno(forge.state)
    testo = forge.make_spec(style_contract="Adotta lo stile noir.")
    for atteso in ("Elena Sartori", "triestina, famiglia slovena", "Molo Audace",
                   "La lettera anonima", "Elena lascia andare il colpevole",
                   "Capitoli brevi, lessico concreto", "Adotta lo stile noir."):
        assert atteso in testo, atteso
    for stampa in ("Rilegatura", "ISBN", "Misura", "Canale"):
        assert stampa not in testo, stampa


def test_a_numbered_list_in_the_plot_is_not_mistaken_for_the_chapters(forge: Forge) -> None:
    """La tabella dei capitoli comanda: «1. Marco arriva in città» nella trama non è un capitolo."""
    _quaderno_pieno(forge.state)
    forge.state.notebook["plot"] = "1. Marco arriva in città\n2. Marco riparte"
    forge.make_spec()
    previsti = planned_chapters(forge.project.spec_path)
    assert previsti == {i: f"Il passaggio {i}" for i in (1, 2, 3)}


def test_chapters_are_written_knowing_the_notebook(forge: Forge) -> None:
    """Chi scrive un capitolo deve sapere chi sono i personaggi e dove si muovono."""
    _quaderno_pieno(forge.state)
    forge.make_spec()
    forge.approve_spec()
    visti: list[str] = []
    originale = forge.client.chat

    def spia(messages, **kw):
        visti.append(messages[-1]["content"])
        return originale(messages, **kw)

    forge.client.chat = spia
    forge.write_next()
    assert any("Elena Sartori" in v and "Molo Audace" in v for v in visti)


def test_editing_the_specification_by_hand_updates_the_structure(forge: Forge) -> None:
    """La specifica la può correggere l'autore in un editor: la struttura si rilegge da lì."""
    _quaderno_pieno(forge.state)
    forge.make_spec()
    testo = forge.project.spec_path.read_text(encoding="utf-8")
    forge.save_spec(testo.replace("Il passaggio 2", "Un titolo scelto a mano"))
    assert [c["title"] for c in forge.state.outline][1] == "Un titolo scelto a mano"


def test_writing_is_blocked_until_the_specification_is_approved(forge: Forge) -> None:
    _quaderno_pieno(forge.state)
    forge.make_spec()
    assert forge.requirements("spec")  # non approvata: manca qualcosa
    forge.approve_spec()
    assert forge.requirements("spec") == []


# --------------------------------------------------------------------------- #
#  fase 4 — la regola zero
# --------------------------------------------------------------------------- #
def _pronto_a_scrivere(forge: Forge) -> None:
    _quaderno_pieno(forge.state)
    forge.make_spec()
    forge.approve_spec()


def test_a_chapter_is_on_disk_before_it_is_reported(forge: Forge) -> None:
    """La regola zero: quello che sta solo in memoria, quando la sessione finisce, non c'è più."""
    _pronto_a_scrivere(forge)
    risultato = forge.write_next()
    assert risultato is not None
    assert risultato.path.exists()
    assert risultato.path.parent == forge.project.chapters_dir
    assert risultato.path.name == "01_il-passaggio-1.md"
    assert risultato.path.read_text(encoding="utf-8").startswith("# Capitolo 1:")


def test_writing_resumes_from_the_files_not_from_memory(forge: Forge) -> None:
    """Riprendendo si riparte dal primo file che manca, non da dove si crede di essere."""
    _pronto_a_scrivere(forge)
    forge.write_next()
    ripreso = Forge(forge.project.root, AppConfig(), client=FintoModello())
    assert [c["number"] for c in wr.pending_chapters(ripreso.project, ripreso.state)] == [2, 3]
    # E se un file sparisce, quel capitolo torna da fare: comanda il disco.
    next(iter(forge.project.written_chapters().values())).unlink()
    assert wr.next_chapter(forge.project, forge.state)["number"] == 1


def test_all_the_chapters_land_on_disk_and_the_report_matches(forge: Forge) -> None:
    _pronto_a_scrivere(forge)
    report = forge.write_all()
    assert len(report.written) == 3
    assert sorted(forge.project.written_chapters()) == [1, 2, 3]
    st = status(forge.project)
    assert st.missing == []
    assert st.words > 0


def test_stopping_keeps_what_is_already_written(forge: Forge) -> None:
    _pronto_a_scrivere(forge)
    fermato = {"ora": False}

    def basta() -> bool:
        if len(forge.project.written_chapters()) >= 1:
            fermato["ora"] = True
        return fermato["ora"]

    report = forge.write_all(should_stop=basta)
    assert report.stopped
    assert len(forge.project.written_chapters()) == 1
    assert report.skipped


def test_the_chapter_heading_is_the_one_the_composer_expects() -> None:
    """Il compositore riconosce i capitoli dal titolo: se il modello lo scrive a modo suo, si corregge."""
    ripulito = wr._clean("Testo senza titolo.", 7, "Il settimo")
    assert ripulito.splitlines()[0] == "# Capitolo 7: Il settimo"
    tenuto = wr._clean("# Capitolo 7: Come l'ha scritto il modello\n\nTesto.", 7, "Il settimo")
    assert tenuto.splitlines()[0] == "# Capitolo 7: Come l'ha scritto il modello"
    scartato = wr._clean("```markdown\n# Un titolo qualunque\n\nTesto.\n```", 2, "Il secondo")
    assert scartato.splitlines()[0] == "# Capitolo 2: Il secondo"


def test_the_status_report_names_the_chapters_that_are_not_there(forge: Forge) -> None:
    """Un buco si riporta com'è: raccontarlo diversamente è come non averlo controllato."""
    _pronto_a_scrivere(forge)
    forge.write_next()
    st = status(forge.project)
    assert st.missing == [2, 3]
    reso = st.render()
    assert "MANCANTI" in reso and "Il passaggio 2" in reso


def test_front_and_back_matter_go_to_their_own_folders(forge: Forge) -> None:
    _pronto_a_scrivere(forge)
    assert forge.write_matter("introduzione").parent == forge.project.front_dir
    assert forge.write_matter("biografia").parent == forge.project.back_dir
    with pytest.raises(ValueError):
        forge.write_matter("una-sezione-inventata")


# --------------------------------------------------------------------------- #
#  fase 5 — produzione
# --------------------------------------------------------------------------- #
def test_the_cover_requirement_is_a_number_you_can_give_the_author() -> None:
    """A5 con 3 mm di abbondanza: 1819×2551 pixel. Il numero si dice, non si aggira."""
    assert prod.cover_requirements("a5") == (1819, 2551)
    controllo = prod.check_cover(None, "a5")
    assert not controllo.ok
    assert "1819×2551" in controllo.message()


def test_a_small_cover_is_reported_not_silently_enlarged(tmp_path: Path) -> None:
    pillow = pytest.importorskip("PIL.Image")
    piccola = tmp_path / "cover.png"
    pillow.new("RGB", (600, 850), "navy").save(piccola)
    controllo = prod.check_cover(piccola, "a5")
    assert not controllo.ok
    assert controllo.actual == (600, 850)
    assert "ingrandire non crea dettaglio" in controllo.message()


def test_book_yaml_takes_the_object_from_production_and_the_book_from_the_notebook(
    forge: Forge,
) -> None:
    """La misura viene dalla tendina della produzione, non dalla conversazione: lì di stampa
    non si parla. Dal quaderno arrivano titolo, lingua e descrizione."""
    yaml = pytest.importorskip("yaml")
    _quaderno_pieno(forge.state)
    forge.state.notebook["language"] = "en"
    forge.cfg.production.trim = "6x9"
    forge.cfg.production.author = "Anna Bianchi"
    dati = yaml.safe_load(forge.produce().read_text(encoding="utf-8"))
    assert dati["book"]["title"] == "La marea di Trieste"
    assert dati["book"]["language"] == "en"
    assert dati["book"]["author"] == "Anna Bianchi"
    assert dati["book"]["description"] == QUADERNO["premise"]
    assert dati["format"]["preset"] == "6x9"
    assert dati["output"]["formats"] == ["pdf", "docx", "epub", "md"], "nel dubbio, tutti"
    assert dati["source"]["pattern"] == "chapters/*.md"
    # E la misura scritta lì è quella che le fasi dopo rileggono.
    assert prod.trim_of(forge.project) == "6x9"


def test_a_book_with_no_title_does_not_get_called_untitled(forge: Forge) -> None:
    """«Untitled» stampato in copertina sembra una scelta: meglio un titolo provvisorio vero."""
    yaml = pytest.importorskip("yaml")
    forge.state.notebook = {"premise": "Come si impara a leggere il mare dalle sue rotte"}
    dati = yaml.safe_load(forge.produce().read_text(encoding="utf-8"))
    assert dati["book"]["title"].startswith("Come si impara")


def test_the_cover_is_copied_next_to_the_book_and_recorded(forge: Forge, tmp_path: Path) -> None:
    pillow = pytest.importorskip("PIL.Image")
    _quaderno_pieno(forge.state)
    forge.produce()
    origine = tmp_path / "arte.png"
    pillow.new("RGB", (1900, 2600), "black").save(origine)
    copiata = forge.set_cover(origine)
    assert copiata.exists() and copiata.parent == forge.project.root
    assert forge.cover_check().ok


# --------------------------------------------------------------------------- #
#  fasi 6 e 7 — composizione, controlli, consegna
# --------------------------------------------------------------------------- #
def test_the_build_exports_without_a_cover_and_does_not_invent_one(forge: Forge) -> None:
    """Senza immagine si esporta lo stesso, senza copertina, e lo si dice: non se ne inventa una."""
    pytest.importorskip("docx")
    _pronto_a_scrivere(forge)
    forge.write_all()
    forge.produce()
    report = forge.build(formats=["pdf", "docx", "epub", "md"])
    assert set(report.produced) == {"pdf", "docx", "epub", "md"}
    assert all(p.exists() for p in report.produced.values())
    assert report.no_cover
    assert any("senza copertina" in a for a in report.warnings)
    assert report.cover_files == [], "nessuna copertina generata"
    assert not list(forge.project.out_dir.glob("*cover*"))


def test_a_long_premise_does_not_stop_the_docx(forge: Forge) -> None:
    """Word non accetta proprietà oltre i 255 caratteri: una premessa lunga fermava tutto."""
    docx = pytest.importorskip("docx")
    _pronto_a_scrivere(forge)
    forge.state.notebook["premise"] = "Una premessa molto lunga. " * 60  # ~1500 caratteri
    forge.write_all()
    forge.produce()
    report = forge.build(formats=["docx"], allow_missing_cover=True)
    proprieta = docx.Document(report.produced["docx"]).core_properties
    assert 0 < len(proprieta.subject) <= 255


def test_the_interior_can_be_composed_as_a_proof(forge: Forge) -> None:
    _pronto_a_scrivere(forge)
    forge.write_all()
    forge.produce()
    report = forge.build(formats=["pdf", "docx", "epub"], allow_missing_cover=True)
    assert set(report.produced) >= {"pdf", "docx", "epub"}
    assert all(p.exists() for p in report.produced.values())
    assert report.pages > 0
    assert any("senza copertina" in a for a in report.warnings)


def test_the_print_checks_run_and_are_reported_whole(forge: Forge) -> None:
    _pronto_a_scrivere(forge)
    forge.write_all()
    forge.produce()
    forge.build(formats=["pdf"], allow_missing_cover=True)
    testo, ok = forge.check()
    assert "pages" in testo
    assert ok is True


def test_the_handover_says_what_is_still_open(forge: Forge) -> None:
    """Un resoconto pulito su un libro che non si è controllato è peggio di nessun resoconto."""
    _pronto_a_scrivere(forge)
    forge.write_all()
    forge.produce()
    report = forge.build(formats=["pdf"], allow_missing_cover=True)
    note = forge.handover(report).read_text(encoding="utf-8")
    for atteso in ("ISBN", "Licenze dei caratteri", "Costola", "Kindle Previewer", "Copertina"):
        assert atteso in note


def test_the_build_says_no_when_there_is_nothing_to_compose(forge: Forge) -> None:
    from bookwriter.forge.build import BuildError

    with pytest.raises(BuildError, match="book.yaml"):
        forge.build()
    forge.produce()
    with pytest.raises(BuildError, match="capitolo"):
        forge.build()


# --------------------------------------------------------------------------- #
#  le sette fasi come sequenza
# --------------------------------------------------------------------------- #
def test_the_phases_run_end_to_end(forge: Forge) -> None:
    _quaderno_pieno(forge.state)
    forge.finish_interview()
    assert forge.advance()[0]
    forge.make_spec()
    forge.approve_spec()
    forge.decline_research()
    forge.write_all()
    forge.produce()
    forge.build(formats=["pdf"], allow_missing_cover=True)
    forge.handover()
    assert [f.state for f in forge.phases()] == ["fatta"] * 8


def test_a_phase_will_not_close_over_a_hole_unless_you_insist(forge: Forge) -> None:
    passato, mancanze = forge.advance()
    assert not passato and "conversazione" in mancanze.lower()
    assert forge.advance(force=True)[0]
    assert forge.state.phase == "spec"


def test_declining_research_says_once_what_it_costs(forge: Forge) -> None:
    testo = forge.decline_research()
    assert "afferma invece di dimostrare" in testo
    assert forge.state.research_declined
    assert forge.requirements("research") == []


# --------------------------------------------------------------------------- #
#  la ricerca sul web
# --------------------------------------------------------------------------- #
def test_the_web_is_not_touched_until_it_is_turned_on() -> None:
    from bookwriter.forge.websearch import SearchError, fetch, search

    spenta = ResearchSettings()
    assert spenta.enabled is False
    with pytest.raises(SearchError, match="spenta"):
        search("qualunque cosa", spenta)
    assert fetch("https://esempio.invalid/pagina", spenta) is None


def test_duckduckgo_results_are_read_and_the_redirect_unwrapped(httpx_mock) -> None:
    from bookwriter.forge.websearch import search

    httpx_mock.add_response(
        url="https://html.duckduckgo.com/html/",
        text="""
        <div class="result">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fesempio.org%2Flibro">
            Un libro sul mare</a>
          <div class="result__snippet">Copre le rotte e le maree.</div>
        </div>
        <div class="result">
          <a class="result__a" href="https://altro.example/pagina">Un altro</a>
        </div>
        """,
    )
    risultati = search("libri sul mare", ResearchSettings(enabled=True, max_results=5))
    assert [r.url for r in risultati] == ["https://esempio.org/libro", "https://altro.example/pagina"]
    assert risultati[0].title == "Un libro sul mare"
    assert "maree" in risultati[0].snippet


def test_a_page_is_read_without_its_navigation(httpx_mock) -> None:
    from bookwriter.forge.websearch import fetch

    httpx_mock.add_response(
        url="https://esempio.org/pagina",
        headers={"content-type": "text/html; charset=utf-8"},
        text="<html><head><title>Il titolo</title></head><body>"
             "<nav>Menu che non è lettura</nav><article><p>Il testo che conta è questo, scritto "
             "in una frase abbastanza lunga da essere vera prosa e da valere una lettura.</p>"
             "</article><footer>Piè di pagina</footer></body></html>",
    )
    pagina = fetch("https://esempio.org/pagina", ResearchSettings(enabled=True, min_page_chars=40))
    assert pagina is not None and pagina.readable
    assert pagina.title == "Il titolo"
    assert "Il testo che conta è questo" in pagina.text
    assert "Menu" not in pagina.text and "Piè di pagina" not in pagina.text


def test_a_search_engine_that_wants_a_key_says_so() -> None:
    from bookwriter.forge.websearch import SearchError, search

    with pytest.raises(SearchError, match="chiave"):
        search("qualcosa", ResearchSettings(enabled=True, engine="brave"))
    with pytest.raises(SearchError, match="istanza"):
        search("qualcosa", ResearchSettings(enabled=True, engine="searxng"))


def test_the_landscape_is_written_to_research_even_without_the_web(forge: Forge) -> None:
    """Senza rete la fase non fallisce: scrive quello che sa e dichiara di non aver cercato."""
    _quaderno_pieno(forge.state)
    report = forge.research_landscape()
    assert report.path is not None and report.path.name == "00_landscape.md"
    testo = report.path.read_text(encoding="utf-8")
    assert "Un libro che c'è" in testo
    assert any("spenta" in a or "Nessun risultato" in a for a in report.warnings)


def test_a_chapter_research_file_carries_the_evidence_level(forge: Forge, monkeypatch) -> None:
    from bookwriter.forge import research as rs

    _pronto_a_scrivere(forge)
    monkeypatch.setattr(
        rs, "_sources_from_model",
        lambda *a, **k: {"findings": [{"claim": "Il mare si alza", "detail": "di 3 mm l'anno",
                                       "level": "B", "source": "IPCC (2023)",
                                       "url": "https://esempio.org"}],
                         "open_questions": ["una cifra non risalita alla fonte"]},
    )
    report = rs.chapter_research(forge.project, forge.state, forge.client,
                                 ResearchSettings(), forge.state.outline[0])
    testo = report.path.read_text(encoding="utf-8")
    assert "Tipo di fonte:** B" in testo
    assert "IPCC (2023)" in testo
    # il contrasto segnalato si mostra, ma nel libro resta com'è scritto finché l'autore non decide
    assert "resta com'è stato scritto" in testo.replace("**", "")
    assert "Non entrano" not in testo
    assert "IPCC (2023)" in (forge.project.research_dir / "sources.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
#  la cartella si legge da sola
# --------------------------------------------------------------------------- #
def test_the_folder_describes_itself_to_whoever_opens_it_next(tmp_path: Path) -> None:
    """Un altro assistente, o l'autore da solo, deve capirci qualcosa senza di noi."""
    progetto = BookProject(tmp_path / "book")
    progetto.scaffold()
    for cartella in (progetto.chapters_dir, progetto.front_dir, progetto.back_dir,
                     progetto.research_dir, progetto.out_dir):
        assert cartella.is_dir()
    # book.yaml non si crea vuoto: la sua presenza dice che la produzione è stata fatta.
    assert not progetto.config_path.exists()


def test_the_chapter_file_names_sort_the_way_the_book_reads(tmp_path: Path) -> None:
    progetto = BookProject(tmp_path / "book")
    progetto.scaffold()
    for numero, titolo in ((10, "Il decimo"), (2, "Il secondo"), (1, "L'apertura")):
        progetto.chapter_path(numero, titolo).write_text(f"# Capitolo {numero}: {titolo}\n",
                                                         encoding="utf-8")
    assert [p.name for p in progetto.chapter_files()] == [
        "01_lapertura.md", "02_il-secondo.md", "10_il-decimo.md",
    ]
    assert sorted(progetto.written_chapters()) == [1, 2, 10]


# --------------------------------------------------------------------------- #
#  mettere da parte, riprendere, ricominciare
# --------------------------------------------------------------------------- #
def _libro_a_meta(tmp_path: Path) -> BookProject:
    """Un libro con qualcosa dentro: due capitoli, la specifica e lo stato."""
    progetto = BookProject(tmp_path / "book")
    progetto.scaffold()
    progetto.spec_path.write_text("# Specifica\n\n| 1 | L'apertura |\n| 2 | Il secondo |\n",
                                  encoding="utf-8")
    for numero, titolo in ((1, "L'apertura"), (2, "Il secondo")):
        progetto.chapter_path(numero, titolo).write_text(
            f"# Capitolo {numero}: {titolo}\n\n" + " ".join(["parola"] * 500), encoding="utf-8")
    stato = ForgeState(phase="writing", spec_approved=True)
    stato.notebook = {"title": "Il libro di prova"}
    progetto.save_state(stato)
    return progetto


def test_a_save_is_one_file_that_carries_the_whole_book(tmp_path: Path) -> None:
    """Un salvataggio si copia su una chiavetta: dev'essere un file solo, non una cartella."""
    progetto = _libro_a_meta(tmp_path)

    info = sv.save_work(progetto, tmp_path / "saves", "prima della riscrittura")

    assert info.path.is_file() and info.path.suffix == ".zip"
    assert info.name == "prima della riscrittura"
    assert info.chapters == 2 and info.words > 900
    assert info.phase == "writing"
    with zipfile.ZipFile(info.path) as archivio:
        dentro = set(archivio.namelist())
    assert "MASTER_SPEC.md" in dentro
    assert "chapters/01_lapertura.md" in dentro
    assert ".forge/state.json" in dentro
    assert sv.MANIFEST_NAME in dentro, "il manifesto viaggia dentro l'archivio, non accanto"


def test_an_empty_book_is_not_worth_a_save(tmp_path: Path) -> None:
    """Chiedere «vuoi salvare?» a chi non ha scritto una riga insegna a rispondere di no."""
    progetto = BookProject(tmp_path / "book")
    progetto.scaffold()  # solo cartelle vuote
    assert sv.has_work(progetto) is False
    with pytest.raises(sv.SaveError):
        sv.save_work(progetto, tmp_path / "saves")


def test_saves_are_listed_newest_first_and_say_what_is_inside(tmp_path: Path) -> None:
    progetto = _libro_a_meta(tmp_path)
    saves = tmp_path / "saves"
    primo = sv.save_work(progetto, saves, "primo")
    secondo = sv.save_work(progetto, saves, "secondo")
    assert primo.path != secondo.path, "due salvataggi nello stesso secondo non si sovrascrivono"

    elenco = sv.list_saves(saves)

    assert [s.name for s in elenco] == ["secondo", "primo"]
    riga = elenco[0].label()
    assert "secondo" in riga and "2 capitoli" in riga and "Stesura" in riga


def test_a_broken_archive_is_skipped_not_fatal(tmp_path: Path) -> None:
    """Un file rovinato in mezzo agli altri non deve impedire di riprendere quelli buoni."""
    progetto = _libro_a_meta(tmp_path)
    saves = tmp_path / "saves"
    sv.save_work(progetto, saves, "buono")
    (saves / "rovinato.zip").write_bytes(b"non e' uno zip")

    elenco = sv.list_saves(saves)

    assert [s.name for s in elenco] == ["buono"]


def test_restoring_puts_the_current_work_aside_first(tmp_path: Path) -> None:
    """Si riprende un salvataggio per rimediare a un errore: non deve poterne causare un altro."""
    progetto = _libro_a_meta(tmp_path)
    saves = tmp_path / "saves"
    salvato = sv.save_work(progetto, saves, "com'era prima")
    # Il lavoro prosegue e prende una direzione sbagliata.
    progetto.chapter_path(3, "Il terzo").write_text("# Capitolo 3\n\nda buttare", encoding="utf-8")
    sv.clear_work(progetto)
    progetto.scaffold()
    progetto.chapter_path(9, "Tutt'altro libro").write_text("# Capitolo 9\n", encoding="utf-8")

    ripreso = sv.restore_save(salvato.path, progetto, backup_dir=saves)

    assert ripreso.name == "com'era prima"
    assert sorted(progetto.written_chapters()) == [1, 2], "torna esattamente il libro salvato"
    assert 9 not in progetto.written_chapters(), "e quello sbagliato non resta in mezzo"
    assert progetto.load_state().phase == "writing"
    # Il lavoro che c'era è stato messo da parte da solo, prima di essere sovrascritto.
    messi_da_parte = [s for s in sv.list_saves(saves) if "prima di riprendere" in s.name]
    assert len(messi_da_parte) == 1
    with zipfile.ZipFile(messi_da_parte[0].path) as archivio:
        assert any("09_" in n for n in archivio.namelist())


def test_an_archive_cannot_write_outside_the_book_folder(tmp_path: Path) -> None:
    """Un salvataggio arriva anche da fuori: uno zip con «..» dentro non si estrae e basta."""
    progetto = _libro_a_meta(tmp_path)
    cattivo = tmp_path / "saves" / "cattivo.zip"
    cattivo.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cattivo, "w") as archivio:
        archivio.writestr(sv.MANIFEST_NAME, '{"name": "innocuo"}')
        archivio.writestr("../../fuori.txt", "non ci devi arrivare")

    with pytest.raises(sv.SaveError, match="esce dalla cartella"):
        sv.restore_save(cattivo, progetto)

    assert not (tmp_path.parent / "fuori.txt").exists()
    assert sorted(progetto.written_chapters()) == [1, 2], "e il libro non viene toccato"


def test_starting_over_empties_the_book_and_nothing_else(tmp_path: Path) -> None:
    progetto = _libro_a_meta(tmp_path)
    accanto = tmp_path / "sources"
    accanto.mkdir()
    (accanto / "documento.md").write_text("materiale importato", encoding="utf-8")

    sv.clear_work(progetto)

    assert not progetto.root.exists()
    assert (accanto / "documento.md").exists(), "i documenti del progetto non c'entrano"
    # E dopo si riparte davvero da zero, senza residui della sessione precedente.
    ripartito = Forge(progetto.root, AppConfig(), client=FintoModello())
    ripartito.start()
    assert ripartito.state.phase == "interview"
    assert ripartito.state.transcript == [] and ripartito.state.notebook == {}


def test_a_folder_that_is_not_a_book_is_never_deleted(tmp_path: Path) -> None:
    """«Ricomincia da capo» su una radice configurata male cancellerebbe i documenti di chi la usa."""
    non_un_libro = tmp_path / "documenti"
    non_un_libro.mkdir()
    (non_un_libro / "tesi.docx").write_bytes(b"anni di lavoro")

    with pytest.raises(sv.SaveError, match="non c'è un libro"):
        sv.clear_work(BookProject(non_un_libro))

    assert (non_un_libro / "tesi.docx").exists()


def test_a_save_of_a_resumed_book_describes_today_not_yesterday(tmp_path: Path) -> None:
    """Un libro ripreso si porta dietro il manifesto di allora: non deve finire nel nuovo archivio.

    Con due voci uguali dentro lo zip l'elenco legge la prima, e il salvataggio di oggi si
    presenterebbe con il nome e i conteggi di ieri.
    """
    progetto = _libro_a_meta(tmp_path)
    saves = tmp_path / "saves"
    primo = sv.save_work(progetto, saves, "ieri")
    sv.restore_save(primo.path, progetto)
    assert (progetto.root / ".forge" / "save.json").exists(), "il manifesto viene ripristinato"
    progetto.chapter_path(3, "Il terzo").write_text("# Capitolo 3\n\nnuovo", encoding="utf-8")

    secondo = sv.save_work(progetto, saves, "oggi")

    with zipfile.ZipFile(secondo.path) as archivio:
        assert archivio.namelist().count(sv.MANIFEST_NAME) == 1
    riletto = sv.read_save(secondo.path)
    assert riletto.name == "oggi" and riletto.chapters == 3


# --------------------------------------------------------------------------- #
#  un capitolo perso non si porta dietro gli altri
# --------------------------------------------------------------------------- #
class ModelloCheInciampa(FintoModello):
    """Come il modello finto, ma fallisce sui capitoli indicati."""

    def __init__(self, *, cade_su: set[int], **kw) -> None:
        super().__init__(**kw)
        self.cade_su = cade_su
        self.tentati: list[int] = []

    def chat(self, messages, *, model=None, temperature=0.0, extra=None):
        utente = messages[-1]["content"]
        numero = 0
        if "Capitolo " in utente:
            pezzo = utente.split("Capitolo ")[-1].split(":")[0].strip()
            numero = int(pezzo) if pezzo.isdigit() else 0
        if numero:
            self.tentati.append(numero)
        if numero in self.cade_su:
            raise LLMError(f"il modello non ha risposto sul capitolo {numero}")
        return super().chat(messages, model=model, temperature=temperature, extra=extra)


def test_one_lost_chapter_does_not_cost_the_others(tmp_path: Path) -> None:
    """Perdere il giro all'undicesimo capitolo significa non tentare nemmeno i nove dopo.

    È il difetto più costoso della stesura: i capitoli seguenti sarebbero venuti bene, e
    l'autore si ritrova con l'errore di uno solo e nient'altro fatto.
    """
    forge = Forge(tmp_path / "book", AppConfig(), client=ModelloCheInciampa(cade_su={2}))
    _pronto_a_scrivere(forge)

    report = forge.write_all()

    assert sorted(forge.project.written_chapters()) == [1, 3], "gli altri si scrivono lo stesso"
    assert [n for n, _t, _m in report.failed] == [2]
    assert "NON RIUSCITI: 1" in report.summary()
    assert "non ha risposto sul capitolo 2" in report.summary()
    # Il file non c'è: al prossimo giro quel capitolo torna da solo fra quelli da fare.
    assert [c["number"] for c in wr.pending_chapters(forge.project, forge.state)] == [2]


def test_a_dead_model_is_recognised_instead_of_retried_twenty_times(tmp_path: Path) -> None:
    """Tre non riusciti di fila non sono il capitolo: è il server, e insistere costa ore."""
    modello = ModelloCheInciampa(cade_su={1, 2, 3, 4, 5}, chapters=5)
    forge = Forge(tmp_path / "book", AppConfig(), client=modello)
    _pronto_a_scrivere(forge)

    report = forge.write_all()

    assert len(report.failed) == wr.CONSECUTIVE_FAILURES
    assert sorted(set(modello.tentati)) == [1, 2, 3], "il quarto non si tenta nemmeno"
    assert report.skipped == [4, 5]
    assert "è il modello o il server" in report.summary()


def test_the_report_never_hides_a_failure_behind_a_count(tmp_path: Path) -> None:
    """Con tutti i capitoli falliti il resoconto diceva «Nessun capitolo scritto» e nient'altro."""
    forge = Forge(tmp_path / "book", AppConfig(), client=ModelloCheInciampa(cade_su={1, 2, 3}))
    _pronto_a_scrivere(forge)

    testo = forge.write_all().summary()

    assert "Nessun capitolo scritto" in testo
    assert "NON RIUSCITI: 3" in testo
    for numero in (1, 2, 3):
        assert f"non ha risposto sul capitolo {numero}" in testo


def _con_copertina(forge: Forge, tmp_path: Path) -> None:
    pillow = pytest.importorskip("PIL.Image")
    _pronto_a_scrivere(forge)
    forge.write_all()
    forge.produce()
    origine = tmp_path / "arte.png"
    pillow.new("RGB", (1900, 2600), "black").save(origine)
    forge.set_cover(origine)


def test_a_copy_without_cover_is_made_for_each_format_that_carries_one(
    forge: Forge, tmp_path: Path,
) -> None:
    pytest.importorskip("docx")
    _con_copertina(forge, tmp_path)
    report = forge.build(formats=["pdf", "docx", "epub", "md"], copy_without_cover=True)
    assert not report.no_cover
    out = forge.project.out_dir
    assert {"pdf", "pdf_interior", "docx", "epub", "epub_nocover", "md"} <= set(report.produced)
    assert report.produced["pdf_interior"].name.endswith("_interior.pdf")
    assert report.produced["epub_nocover"].name.endswith("_ebook_nocover.epub")
    # nessun doppione: il DOCX e il Markdown non hanno mai copertina e non si duplicano
    assert not list(out.glob("*nocover*.docx")) and not list(out.glob("*nocover*.md"))
    assert any("DOCX" in n for n in report.notes) and any("Markdown" in n for n in report.notes)
    # la copia senza copertina è davvero un'altra cosa dall'EPUB con copertina
    assert (report.produced["epub"].read_bytes() != report.produced["epub_nocover"].read_bytes())
    # senza la richiesta, l'EPUB senza copertina non c'è
    altro = forge.build(formats=["epub"])
    assert "epub_nocover" not in altro.produced


def test_without_a_cover_the_extra_copy_adds_nothing(forge: Forge) -> None:
    _pronto_a_scrivere(forge)
    forge.write_all()
    forge.produce()
    report = forge.build(formats=["pdf", "epub"], copy_without_cover=True)
    assert report.no_cover
    assert set(report.produced) == {"pdf", "epub"}
    assert any("già senza copertina" in n for n in report.notes)


def test_the_markdown_export_is_the_whole_book_in_order(forge: Forge) -> None:
    _pronto_a_scrivere(forge)
    forge.write_all()
    forge.write_matter("introduzione")
    forge.produce()
    report = forge.build(formats=["md"])
    testo = report.produced["md"].read_text(encoding="utf-8")
    posti = [testo.index("# Introduzione")] + [testo.index(f"# Capitolo {n}:") for n in (1, 2, 3)]
    assert posti == sorted(posti), "pagine iniziali, poi i capitoli in ordine"
    assert report.produced["md"].parent == forge.project.out_dir


def test_the_default_book_yaml_lists_markdown(forge: Forge) -> None:
    _quaderno_pieno(forge.state)
    assert "md" in forge.produce().read_text(encoding="utf-8").split("formats:")[1].split("]")[0]


def test_the_cli_builds_markdown_and_still_accepts_the_old_flag(
    forge: Forge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from bookwriter import cli

    _pronto_a_scrivere(forge)
    forge.write_all()
    forge.produce()
    monkeypatch.setattr(cli, "_forge", lambda **k: forge)
    esito = CliRunner().invoke(cli.forge_app, ["build", "--only", "md", "--allow-missing-cover"])
    assert esito.exit_code == 0, esito.output
    assert (forge.project.out_dir / f"{forge.project.out_dir.parent.name}.md").exists() or \
        list(forge.project.out_dir.glob("*.md"))
    assert "senza copertina" in esito.output
