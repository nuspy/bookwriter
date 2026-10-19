"""Forge: dal colloquio con l'autore al libro stampabile, in sette fasi.

Il metodo è quello di *book-forge* (``assets/METHOD.md``, con i suoi riferimenti in
``assets/references/``): un assistente intervista l'autore, scrive una specifica, fa la ricerca,
stende i capitoli, prepara la produzione, compone ed esporta, e alla fine dichiara cosa resta
aperto.

1. **Intervista** — una conversazione con l'autore per capire il libro: trama, personaggi, scopo
2. **Specifica** — scrivere ``MASTER_SPEC.md`` e farla approvare
3. **Ricerca** — raccogliere le fonti, e lasciare che siano loro a formare la struttura
4. **Stesura** — scrivere capitolo per capitolo contro la specifica
5. **Produzione** — copertina, ``book.yaml``, misura, tipografia
6. **Composizione** — comporre ed esportare
7. **Controlli e consegna** — verificare, riferire, elencare cosa resta

L'ordine non è decorativo: la struttura si progetta su quello che è emerso dalla conversazione,
e ogni capitolo si scrive contro la struttura approvata. Della stampa nella conversazione non si
parla: misura, copertina e formati si decidono a mano nella fase 5, che ha una dipendenza dura —
**la copertina la fornisce l'autore**: non la si inventa e non la si genera senza che sia stato
chiesto.

E la regola che viene prima di tutte: **ogni capitolo finisce in ``<libro>/chapters/NN_nome.md``
appena è scritto, prima che cominci il successivo.** Un libro dura più sessioni di lavoro, il
contesto di un modello no; quel che non è su disco quando la sessione finisce non lo recupera
nessuno. Per questo :func:`status` guarda il disco e non la memoria.
"""

from bookwriter.forge.build import BuildError, BuildReport, CoverMissing, build, check
from bookwriter.forge.compose import (
    Composed,
    book_config,
    book_from_markdown,
    compose_markdown,
    render_book,
)
from bookwriter.forge.engine import Forge, PhaseInfo, open_forge
from bookwriter.forge.project import (
    PHASES,
    BookProject,
    ForgeState,
    Status,
    phase_title,
    status,
)
from bookwriter.forge.saves import (
    SaveError,
    SaveInfo,
    clear_work,
    has_work,
    list_saves,
    read_save,
    restore_save,
    save_work,
)

__all__ = [
    "PHASES",
    "BookProject",
    "BuildError",
    "BuildReport",
    "Composed",
    "CoverMissing",
    "Forge",
    "ForgeState",
    "PhaseInfo",
    "SaveError",
    "SaveInfo",
    "Status",
    "book_config",
    "book_from_markdown",
    "build",
    "check",
    "clear_work",
    "compose_markdown",
    "has_work",
    "list_saves",
    "open_forge",
    "phase_title",
    "read_save",
    "render_book",
    "restore_save",
    "save_work",
    "status",
]
