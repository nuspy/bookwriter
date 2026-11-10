"""Motore di stile: estrazione, definizione, fusione autori e applicazione (§6.2).

- ``extract_style`` — da un testo campione → StyleCard.
- ``abstract_style`` — da una descrizione in linguaggio naturale → StyleCard.
- ``authors_style`` + ``blend_styles`` — imita uno o più autori con **pesi** e **ambiti**.
- ``build_style_contract`` / ``apply_style`` — contratto di stile + riscrittura con critic loop.

Si imita lo *stile* (pastiche), non si riproduce testo protetto alla lettera.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from bookwriter.style.models import STYLE_DIMENSIONS, STYLE_SCHEMA, AuthorWeight, StyleProfile

_CRITIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "conforms": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["conforms", "issues"],
}

_DIMS_LIST = ", ".join(STYLE_DIMENSIONS)


class SupportsLLM(Protocol):
    def chat(self, messages: list[dict[str, str]], *, model: str | None = ...) -> str:
        ...

    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


def _card(client: SupportsLLM, system: str, user: str, model: str | None) -> dict:
    return client.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        schema=STYLE_SCHEMA,
        model=model,
    )


# Quanto testo si legge per imparare uno stile. Seimila caratteri erano due pagine: la voce di
# un autore si riconosce sulla distanza, fra dialoghi, descrizioni e digressioni.
_STYLE_SAMPLE_CHARS = 40_000


def extract_style(text: str, client: SupportsLLM, *, name: str, model: str | None = None) -> StyleProfile:
    system = (
        f"Analizza lo STILE del testo (non il contenuto). Descrivi in modo conciso ciascuna "
        f"dimensione ({_DIMS_LIST}). Fornisci 2-3 exemplars (brevi frasi originali che imitano lo "
        f"stile) e i vincoli. Non copiare frasi lunghe dal testo."
    )
    return StyleProfile.from_card(
        name, "extracted", _card(client, system, text[:_STYLE_SAMPLE_CHARS], model)
    )


def campione_rappresentativo(testi: list[str], limite: int = _STYLE_SAMPLE_CHARS) -> str:
    """Brani presi a intervalli regolari lungo tutto il libro, nel loro ordine, fino a ``limite``.

    Le prime pagine di un libro non sono il libro: prefazione, dedica, un incipit più sorvegliato
    del resto. Lo stile si legge sulla distanza, e un campione che salta da un capo all'altro
    vede dialoghi, descrizioni e digressioni invece di quaranta pagine dello stesso capitolo.
    """
    pieni = [t.strip() for t in testi if t and t.strip()]
    if not pieni:
        return ""
    media = max(1, sum(len(t) for t in pieni) // len(pieni))
    quanti = max(1, min(len(pieni), limite // media))
    if quanti >= len(pieni):
        scelti = pieni
    elif quanti == 1:
        scelti = [pieni[len(pieni) // 2]]
    else:
        indici = sorted({round(i * (len(pieni) - 1) / (quanti - 1)) for i in range(quanti)})
        scelti = [pieni[i] for i in indici]
    return "\n\n[…]\n\n".join(scelti)[:limite]


def extract_source_style(
    testi: list[str], client: SupportsLLM, *, name: str, model: str | None = None
) -> StyleProfile:
    """Lo stile di un libro intero, letto su un campione distribuito (vedi sopra).

    Serve alla traduzione: lo stile da rendere nella lingua di arrivo è quello dell'originale,
    e descriverlo in dimensioni concrete (ritmo, registro, lunghezza delle frasi, immagini)
    guida il traduttore molto più di un generico «mantieni lo stile».
    """
    return extract_style(campione_rappresentativo(testi), client, name=name, model=model)


def abstract_style(description: str, client: SupportsLLM, *, name: str, model: str | None = None) -> StyleProfile:
    system = (
        f"L'utente descrive lo stile desiderato. Traducilo in descrittori concreti per ciascuna "
        f"dimensione ({_DIMS_LIST}), con 2-3 exemplars illustrativi e i vincoli."
    )
    return StyleProfile.from_card(name, "abstract", _card(client, system, description, model))


def _author_card(author: str, client: SupportsLLM, model: str | None) -> StyleProfile:
    system = (
        f"Descrivi lo STILE dell'autore indicato come pastiche (senza copiare testo protetto). "
        f"Compila ciascuna dimensione ({_DIMS_LIST}), con 2-3 exemplars originali e i vincoli."
    )
    return StyleProfile.from_card(author, "authors", _card(client, system, f"Autore: {author}", model))


def blend_styles(name: str, pairs: list[tuple[AuthorWeight, StyleProfile]]) -> StyleProfile:
    """Fonde più StyleProfile **per dimensione** secondo pesi e ambiti degli autori."""
    dims: dict[str, str] = {}
    for dim in STYLE_DIMENSIONS:
        contributors = [(w, p) for w, p in pairs if not w.dims or dim in w.dims] or pairs
        total = sum(w.weight for w, _ in contributors) or 1.0
        parts = []
        for w, prof in sorted(contributors, key=lambda x: -x[0].weight):
            desc = prof.dimensions.get(dim, "")
            if desc:
                parts.append(f"{round(100 * w.weight / total)}% {w.author}: {desc}")
        dims[dim] = " ; ".join(parts)
    exemplars = [e for _, prof in pairs for e in prof.exemplars][:6]
    constraints = sorted({c for _, prof in pairs for c in prof.constraints})
    return StyleProfile(
        name=name,
        origin="authors",
        dimensions=dims,
        exemplars=exemplars,
        constraints=constraints,
        author_blend=[w for w, _ in pairs],
    )


def authors_style(
    specs: list[AuthorWeight],
    client: SupportsLLM,
    *,
    name: str,
    model: str | None = None,
) -> StyleProfile:
    pairs = [(spec, _author_card(spec.author, client, model)) for spec in specs]
    return blend_styles(name, pairs)


def build_style_contract(profile: StyleProfile) -> str:
    """Trasforma il profilo in un 'contratto di stile' da usare come system prompt."""
    lines = [f"Adotta in modo UNIFORME il seguente stile (profilo: {profile.name})."]
    for dim in STYLE_DIMENSIONS:
        desc = profile.dimensions.get(dim)
        if desc:
            lines.append(f"- {dim}: {desc}")
    if profile.constraints:
        lines.append("Vincoli: " + "; ".join(profile.constraints))
    if profile.exemplars:
        lines.append("Esempi di stile: " + " | ".join(profile.exemplars[:3]))
    return "\n".join(lines)


#: Un pezzo riscritto che perde più di un decimo delle parole ha tagliato contenuto, non stile.
LENGTH_GUARD = 0.9
#: Quante parole al massimo per pezzo: quanto un modello riscrive bene in una risposta.
STYLE_CHUNK_WORDS = 1500


def _words(testo: str) -> int:
    return len(re.findall(r"[^\W\d_]+", testo or ""))


def _style_chunks(text: str) -> list[str]:
    """Il testo in pezzi da riscrivere: le sezioni ``#``, e quelle troppo lunghe a paragrafi."""
    sezioni: list[str] = []
    corrente: list[str] = []
    for riga in (text or "").splitlines():
        if riga.lstrip().startswith("#") and corrente and any(r.strip() for r in corrente):
            sezioni.append("\n".join(corrente).strip())
            corrente = []
        corrente.append(riga)
    if any(r.strip() for r in corrente):
        sezioni.append("\n".join(corrente).strip())
    pezzi: list[str] = []
    for sezione in sezioni:
        if _words(sezione) <= STYLE_CHUNK_WORDS:
            pezzi.append(sezione)
            continue
        blocco: list[str] = []
        for paragrafo in re.split(r"\n\s*\n", sezione):
            if blocco and _words("\n\n".join([*blocco, paragrafo])) > STYLE_CHUNK_WORDS:
                pezzi.append("\n\n".join(blocco))
                blocco = []
            blocco.append(paragrafo)
        if blocco:
            pezzi.append("\n\n".join(blocco))
    return pezzi or [text]


def apply_style(
    text: str,
    profile: StyleProfile,
    client: SupportsLLM,
    *,
    model: str | None = None,
    critic: bool = True,
) -> str:
    """Riscrive ``text`` nello stile del profilo, un pezzo alla volta.

    Un testo lungo non si riscrive in una risposta sola: il modello ne consegna un terzo e lo fa
    sembrare finito (un capitolo da 7.200 parole tornato da 3.100). Si lavora per sezioni ``#``,
    e ogni pezzo che torna più corto di un decimo si richiede una volta; se è ancora corto, resta
    com'era: meglio un pezzo nello stile di prima che un pezzo mutilato.
    """
    pezzi = _style_chunks(text)
    if len(pezzi) > 1:
        return "\n\n".join(apply_style(p, profile, client, model=model, critic=critic)
                           for p in pezzi)
    contract = build_style_contract(profile)
    instruction = (
        "Riscrivi il testo mantenendo fedele il CONTENUTO (nessuna informazione aggiunta o tolta), "
        "ma adottando lo stile descritto. Rispondi solo con il testo riscritto."
    )
    out = client.chat(
        [{"role": "system", "content": contract + "\n" + instruction}, {"role": "user", "content": text}],
        model=model,
    ).strip()
    if _words(out) < LENGTH_GUARD * _words(text):
        out = client.chat(
            [{"role": "system", "content": contract + "\n" + instruction},
             {"role": "user", "content": text},
             {"role": "assistant", "content": out},
             {"role": "user", "content": "Hai accorciato il testo: riscrivilo per intero, senza "
                                         "togliere niente, paragrafo per paragrafo."}],
            model=model,
        ).strip()
        if _words(out) < LENGTH_GUARD * _words(text):
            return text

    if critic:
        verdict = client.chat_json(
            [
                {"role": "system", "content": "Verifica se il testo rispetta il contratto di stile. Elenca gli scostamenti."},
                {"role": "user", "content": contract + "\n\nTESTO:\n" + out},
            ],
            schema=_CRITIC_SCHEMA,
            model=model,
        )
        if not verdict.get("conforms", True) and verdict.get("issues"):
            fix = "Correggi questi scostamenti dallo stile: " + "; ".join(verdict["issues"])
            out = client.chat(
                [
                    {"role": "system", "content": contract + "\n" + instruction + "\n" + fix},
                    {"role": "user", "content": out},
                ],
                model=model,
            ).strip()
    return out
