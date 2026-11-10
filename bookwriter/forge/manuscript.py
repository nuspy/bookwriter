"""Un manoscritto che esiste già, portato dentro la macchina a fasi.

«Completa un libro» e «Aggiorna un libro» partono da un testo scritto: lo si legge, lo si divide in
capitoli, e lo si mette dove la stesura lo trova — ``chapters/``, come se l'avesse scritto lei —
tenendone una copia intatta in ``originale/``, che nessun passaggio tocca mai.

Dal manoscritto si ricavano anche le cose che altrimenti chiederebbe l'intervista:

* il **registro della continuità** (:mod:`ledger`), capitolo per capitolo: cosa succede, dove
  finisce, cosa sa il lettore, i motivi e le invenzioni;
* il **quaderno** (personaggi, luoghi, trama, ambientazione): l'intervista parte già informata e
  l'autore corregge invece di raccontare tutto da capo;
* la **voce**: un profilo di stile ricavato dal testo, scelto come stile del libro, e brani
  originali da dare alla stesura come esempio — le parti nuove devono suonare come le vecchie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bookwriter.forge import ledger as lg
from bookwriter.forge import notebook as nb
from bookwriter.forge.metrics import count_words, paragraphs, prose_only
from bookwriter.forge.project import BookProject, ForgeState, slugify

#: Le righe che aprono un capitolo in un testo senza titoli Markdown.
_CHAPTER_LINE = re.compile(
    r"^\s*(?:#+\s*)?(?:capitolo|chapter|chapitre|kapitel|cap\.)\s+([0-9IVXLCM]+|\w+)\b[^\n]*$",
    re.IGNORECASE | re.MULTILINE)

#: Quante parole di testo originale vanno alla stesura come esempio della voce.
VOICE_WORDS = 450


@dataclass
class ManuscriptChapter:
    title: str
    text: str


def split_manuscript(text: str) -> list[ManuscriptChapter]:
    """Il manoscritto diviso in capitoli.

    Prima i titoli Markdown del livello che dà più di un capitolo (``#``, poi ``##``); poi le
    righe «Capitolo N» / «Chapter N»; se non c'è niente, un capitolo solo.
    """
    testo = (text or "").replace("\r\n", "\n").strip()
    for livello in ("# ", "## "):
        schema = re.compile(rf"^{re.escape(livello)}(.+)$", re.MULTILINE)
        trovati = list(schema.finditer(testo))
        if len(trovati) >= 2:
            return _cut(testo, [(m.start(), m.end(), m.group(1).strip()) for m in trovati])
    righe = list(_CHAPTER_LINE.finditer(testo))
    if len(righe) >= 2:
        return _cut(testo, [(m.start(), m.end(), m.group(0).strip("# ").strip()) for m in righe])
    return [ManuscriptChapter(title="", text=testo)] if testo else []


def _cut(testo: str, punti: list[tuple[int, int, str]]) -> list[ManuscriptChapter]:
    capitoli = []
    for i, (inizio, fine_titolo, titolo) in enumerate(punti):
        fine = punti[i + 1][0] if i + 1 < len(punti) else len(testo)
        corpo = testo[fine_titolo:fine].strip()
        if corpo or titolo:
            capitoli.append(ManuscriptChapter(title=_clean_title(titolo), text=corpo))
    prima = testo[:punti[0][0]].strip()
    if prima and count_words(prima) > 150 and capitoli:
        # un prologo senza titolo davanti al primo capitolo
        capitoli.insert(0, ManuscriptChapter(title="Prologo", text=prima))
    return capitoli


def _clean_title(titolo: str) -> str:
    """«Capitolo 3: Il molo» → «Il molo»; «Capitolo 3» resta com'è."""
    pulito = re.sub(r"^(?:capitolo|chapter|chapitre|kapitel|cap\.)\s+[0-9IVXLCM]+\s*[:.\-–—]?\s*",
                    "", titolo.strip(), flags=re.IGNORECASE)
    return pulito or titolo.strip()


def chapter_text(numero: int, titolo: str, corpo: str) -> str:
    """Il file di un capitolo, nella forma che il compositore si aspetta."""
    return f"# Capitolo {numero}: {titolo}\n\n{corpo.strip()}\n"


def original_path(project: BookProject, number: int, title: str) -> Path:
    return project.original_dir / f"{number:02d}_{slugify(title)}.md"


def originals(project: BookProject) -> dict[int, Path]:
    """I capitoli originali, per numero (quello del manoscritto)."""
    if not project.original_dir.exists():
        return {}
    trovati = {}
    for p in sorted(project.original_dir.glob("*.md")):
        m = re.match(r"^(\d+)", p.stem)
        if m:
            trovati[int(m.group(1))] = p
    return trovati


# --------------------------------------------------------------------------- #
#  l'importazione
# --------------------------------------------------------------------------- #
def import_text(project: BookProject, state: ForgeState, text: str, *, source: str = "") -> int:
    """Mette il manoscritto nel libro: ``originale/`` intatto, ``chapters/`` da lavorare, e la
    struttura con un capitolo per capitolo originale. Ritorna quanti capitoli.

    Non si importa due volte sopra un lavoro cominciato: chi vuole ripartire da un altro
    manoscritto ricomincia il libro (o usa un altro progetto).
    """
    if state.manuscript.get("imported"):
        raise ValueError("Il manoscritto è già importato: per partire da un altro, ricomincia "
                         "il libro da capo.")
    capitoli = split_manuscript(text)
    if not capitoli or not any(c.text.strip() for c in capitoli):
        raise ValueError("Il documento non contiene testo leggibile.")
    project.scaffold()
    project.original_dir.mkdir(parents=True, exist_ok=True)
    struttura = []
    for i, c in enumerate(capitoli, 1):
        titolo = c.title or f"Capitolo {i}"
        testo = chapter_text(i, titolo, c.text)
        original_path(project, i, titolo).write_text(testo, encoding="utf-8")
        project.chapter_path(i, titolo).write_text(testo, encoding="utf-8")
        struttura.append({"number": i, "part": 1, "title": titolo, "does": "",
                          "words": count_words(c.text), "points": [], "status": "originale",
                          "source": i, "file": project.chapter_path(i, titolo).name})
    state.outline = struttura
    state.manuscript = {
        "file": source, "chapters": len(capitoli),
        "words": sum(count_words(c.text) for c in capitoli),
        "imported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return len(capitoli)


def summary_from_ledger(project: BookProject) -> str:
    """Il manoscritto in un capitolo per riga, dal registro: per l'intervista e per il piano."""
    registro = lg.load(project)
    righe = []
    for n in sorted(registro.chapters):
        v = registro.chapters[n]
        fine = v.end_state.place
        righe.append(f"- Capitolo {n} «{v.title}» ({v.words} parole): {v.summary}"
                     + (f" Finisce: {fine}." if fine else ""))
    return "\n".join(righe)


# --------------------------------------------------------------------------- #
#  il quaderno, dal manoscritto
# --------------------------------------------------------------------------- #
def notebook_from_manuscript(state: ForgeState, client: Any, summary: str, sample: str, *,
                             model: str | None = None) -> dict[str, Any]:
    """Il quaderno ricavato dal manoscritto: quello che il testo dice, non quello che si spera.

    Si fonde con quello che c'è (l'autore può aver già parlato). Non solleva: se il modello non
    risponde, il quaderno resta com'era e l'intervista lo riempie parlando.
    """
    sistema = (
        "Ricavi il quaderno di un libro da un manoscritto che esiste già: chi sono i personaggi "
        "(aspetto, carattere, cultura, come cambiano), i luoghi, l'ambientazione e l'epoca, la "
        "trama e la catena degli avvenimenti, i nessi causali, le scene chiave, il finale (se "
        "c'è), il genere e la forma, il lettore a cui sembra rivolto, la lingua. Scrivi solo "
        "quello che il testo dice o mostra; dove non dice niente lascia vuoto. 'open_questions', "
        "'agreed', 'assistant_decisions' e 'changes' restano vuoti: li riempie la conversazione "
        f"con l'autore. Scrivi in {nb.language_name(state)}."
    )
    utente = f"# Il manoscritto, capitolo per capitolo\n{summary}\n\n# Un brano del testo\n{sample}"
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=nb.schema(with_removed=False), model=model)
    except Exception:  # noqa: BLE001 - l'intervista lo riempie parlando
        return state.notebook
    if not isinstance(r, dict):
        return state.notebook
    nuovo = nb.normalise(r)
    for chiave in ("open_questions", "agreed", "assistant_decisions", "changes"):
        nuovo[chiave] = []
    return nb.merge(nb.normalise(state.notebook), nuovo)


def sample_text(project: BookProject, words: int = 2500) -> str:
    """Un campione del manoscritto per ricavarne la voce: l'inizio di più capitoli, non uno solo."""
    files = list(originals(project).values())
    if not files:
        return ""
    quota = max(300, words // max(1, min(len(files), 5)))
    pezzi = []
    for p in files[:: max(1, len(files) // 5)][:5]:
        corpo = " ".join(prose_only(p.read_text(encoding="utf-8")).split()[:quota * 2])
        pezzi.append(" ".join(corpo.split()[:quota]))
    return "\n\n[…]\n\n".join(pezzi)


def voice_sample(project: BookProject, number: int, words: int = VOICE_WORDS) -> str:
    """Un brano originale vicino al capitolo ``number``: alla stesura, perché imiti la voce.

    Si prende il capitolo originale più vicino (quello stesso, se esiste: per completarlo), e da
    lì i paragrafi interi fino alla misura — paragrafi di narrazione, non i titoli.
    """
    orig = originals(project)
    if not orig:
        return ""
    vicino = min(orig, key=lambda n: (abs(n - number), -n))
    tutti = paragraphs(orig[vicino].read_text(encoding="utf-8"))
    blocchi = [b for b in tutti if count_words(b) >= 25] or tutti
    fuori, conto = [], 0
    for b in blocchi[len(blocchi) // 3:] + blocchi[: len(blocchi) // 3]:
        fuori.append(b)
        conto += count_words(b)
        if conto >= words:
            break
    return "\n\n".join(fuori)
