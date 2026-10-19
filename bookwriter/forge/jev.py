"""Giudizi rapidi della ricerca con Jev (TypeSafe, https://typesafe.ai).

Jev è un modello «System One»: si manda uno ``state`` e delle domande tipizzate, e risponde in
un quarto di secondo con numeri, non con testo. Qui serve a due giudizi che il modello locale
paga con migliaia di token di ragionamento:

* la **pertinenza** di una pagina per il punto che si sta scrivendo (:meth:`JevSession.judge`);
* la scelta, dentro una pagina lunga o un PDF, delle **sezioni** più utili al punto, che sono poi
  le sole che il modello locale legge per l'estrazione (:meth:`JevSession.best_excerpt`).

Cosa esce dal computer: **solo estratti** (al più ``jev_max_chars`` caratteri per richiesta, mai
una pagina intera né un PDF) e il punto del capitolo che si sta cercando. Mai la premessa del
libro. Spento per impostazione: senza chiave o con ``jev_enabled`` falso non si manda niente a
nessuno. Se Jev non risponde, chi chiama ripiega sul modello locale (comportamento di sempre).

La chiave si legge dalle impostazioni o da ``TYPESAFE_API_KEY`` e **non compare mai** nei
messaggi d'errore né nei log. Solo HTTP (httpx, già presente): l'SDK non serve.
"""

from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_API_KEY"

#: I livelli della domanda, dal peggiore al migliore: la risposta è un punteggio da 0 a 3.
CRITERIA = ["Off-topic, unrelated", "Barely related", "Useful", "Very useful"]
INSTRUCTIONS = ("How useful is this web page for writing the book passage described at the top")
QUESTION_ID = "utile"
SCALE_MAX = float(len(CRITERIA) - 1)

#: Quante sezioni di un documento si fanno giudicare al più. Oltre, se ne giudica un sottoinsieme
#: a intervalli regolari: un PDF di trecento pagine costerebbe centinaia di richieste.
MAX_SECTIONS = 60
#: Quanto è lunga, al più, una sezione (caratteri). Le sezioni si passano al modello intere.
SECTION_CHARS = 1500
#: Richieste insieme.
PARALLEL = 4


class JevError(RuntimeError):
    """Jev non ha risposto o ha risposto qualcosa di inservibile. Chi chiama ripiega."""


@dataclass
class JevMeter:
    """Quanto è stato speso con Jev: si salva nei dati del capitolo, accanto alla ricerca."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    failures: int = 0
    fallbacks: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(self, *, input_tokens: int = 0, output_tokens: int = 0, seconds: float = 0.0) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.seconds += seconds

    def failed(self) -> None:
        with self._lock:
            self.failures += 1

    def fell_back(self) -> None:
        with self._lock:
            self.fallbacks += 1

    def as_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "seconds": round(self.seconds, 2),
                "failures": self.failures, "fallbacks": self.fallbacks}

    def merge_into(self, target: dict[str, Any]) -> None:
        """Somma questi conti a quelli già salvati (un capitolo si riprende in più volte)."""
        vecchio = dict(target)
        nuovo = self.as_dict()
        for chiave, valore in nuovo.items():
            target[chiave] = round(float(vecchio.get(chiave, 0)) + valore, 2) \
                if chiave == "seconds" else int(vecchio.get(chiave, 0)) + valore


def api_key_for(settings: Any) -> str:
    """La chiave: quella delle impostazioni, o la variabile d'ambiente. Mai stampata."""
    return (str(getattr(settings, "jev_api_key", "") or "").strip()
            or os.environ.get(KEY_ENV, "").strip())


def _clean(message: str, key: str) -> str:
    """Un messaggio d'errore senza la chiave (e senza andare a capo)."""
    testo = " ".join(str(message).split())
    return testo.replace(key, "***") if key else testo


class JevClient:
    """Una richiesta alla volta a Jev. Non tiene la chiave in nessun messaggio."""

    def __init__(self, key: str, *, timeout_s: float = 20.0) -> None:
        self._key = key
        self.timeout_s = timeout_s

    def __repr__(self) -> str:  # la chiave non finisce nei log nemmeno per sbaglio
        return f"JevClient(timeout_s={self.timeout_s})"

    def score(self, state: str, meter: JevMeter | None = None) -> tuple[float, float]:
        """``(punteggio 0-3, confidenza 0-1)`` per lo stato dato. Solleva :class:`JevError`."""
        corpo = {
            "state": state, "model": MODEL,
            "questions": {QUESTION_ID: {"type": "score", "instructions": INSTRUCTIONS,
                                        "criteria": CRITERIA}},
        }
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_s) as c:
                r = c.post(URL, json=corpo, headers={
                    "Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            if meter:
                meter.failed()
            raise JevError(_clean(f"Jev non risponde ({type(exc).__name__})", self._key)) from exc
        if r.status_code != 200:
            if meter:
                meter.failed()
            raise JevError(_clean(f"Jev ha risposto HTTP {r.status_code}", self._key))
        try:
            dati = r.json()
            risposta = dati["answers"][QUESTION_ID]
            punteggio, confidenza = float(risposta["score"]), float(risposta.get("confidence", 0))
            uso = dati.get("usage") or {}
        except (ValueError, KeyError, TypeError) as exc:
            if meter:
                meter.failed()
            raise JevError("Jev ha risposto in un formato che non conosco") from exc
        if meter:
            meter.add(input_tokens=int(uso.get("input_tokens") or 0),
                      output_tokens=int(uso.get("output_tokens") or 0),
                      seconds=time.perf_counter() - t0)
        return max(0.0, min(SCALE_MAX, punteggio)), max(0.0, min(1.0, confidenza))


def split_sections(text: str, max_len: int = SECTION_CHARS) -> list[str]:
    """Il testo in sezioni intere di al più ``max_len`` caratteri: paragrafi (righe vuote)
    accorpati finché ci stanno; un paragrafo più lungo si divide per frasi."""
    paragrafi = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pezzi: list[str] = []
    for p in paragrafi:
        if len(p) <= max_len:
            pezzi.append(p)
            continue
        corrente = ""
        for frase in re.split(r"(?<=[.!?…])\s+", p):
            while len(frase) > max_len:           # una frase sola più lunga del limite
                if corrente:
                    pezzi.append(corrente)
                    corrente = ""
                pezzi.append(frase[:max_len])
                frase = frase[max_len:]
            if corrente and len(corrente) + 1 + len(frase) > max_len:
                pezzi.append(corrente)
                corrente = frase
            else:
                corrente = f"{corrente} {frase}".strip()
        if corrente:
            pezzi.append(corrente)
    sezioni: list[str] = []
    for p in pezzi:
        if sezioni and len(sezioni[-1]) + 2 + len(p) <= max_len:
            sezioni[-1] += "\n\n" + p
        else:
            sezioni.append(p)
    return sezioni


class JevSession:
    """Jev per un lavoro (un capitolo): il client, le soglie e il conto di quanto si spende."""

    def __init__(self, client: JevClient, *, min_score: float, max_chars: int) -> None:
        self.client = client
        self.min_score = min_score
        self.max_chars = max(200, int(max_chars))
        self.meter = JevMeter()

    def _state(self, context: str, text: str) -> str:
        # Mai oltre il limite: né una pagina intera né un PDF escono dal computer.
        return f"BOOK PASSAGE: {context}\n\nWEB PAGE:\n{text[:self.max_chars]}"

    def judge(self, context: str, text: str) -> tuple[float, float]:
        """Quanto serve la pagina al punto: ``(punteggio 0-3, confidenza)``. Solleva JevError."""
        return self.client.score(self._state(context, text), self.meter)

    def best_excerpt(self, context: str, text: str, budget: int) -> str:
        """Le sezioni più utili al punto, **intere**, entro ``budget`` caratteri e nell'ordine in
        cui stanno nel testo. Un testo che sta già nel budget torna com'è, senza chiamate. Una
        sezione per volta a Jev; se una richiesta fallisce si ripiega sull'inizio del testo."""
        if len(text) <= budget:
            return text
        sezioni = split_sections(text, min(SECTION_CHARS, self.max_chars, max(200, budget // 2)))
        indici = list(range(len(sezioni)))
        if len(indici) > MAX_SECTIONS:
            passo = len(indici) / MAX_SECTIONS
            indici = sorted({int(i * passo) for i in range(MAX_SECTIONS)})
        try:
            with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
                punteggi = list(pool.map(
                    lambda i: self.client.score(self._state(context, sezioni[i]), self.meter)[0],
                    indici))
        except JevError:
            self.meter.fell_back()
            return text[:budget]
        scelte: list[int] = []
        usato = 0
        for i, _ in sorted(zip(indici, punteggi), key=lambda x: (-x[1], x[0])):
            costo = len(sezioni[i]) + 7  # la sezione e il separatore «[…]»
            if usato + costo > budget:
                continue
            scelte.append(i)
            usato += costo
        if not scelte:
            self.meter.fell_back()
            return text[:budget]
        return "\n\n[…]\n\n".join(sezioni[i] for i in sorted(scelte))


def session(settings: Any) -> JevSession | None:
    """La sessione Jev per queste impostazioni, o ``None`` se Jev è spento o manca la chiave."""
    if not getattr(settings, "jev_enabled", False):
        return None
    chiave = api_key_for(settings)
    if not chiave:
        return None
    return JevSession(JevClient(chiave, timeout_s=float(getattr(settings, "jev_timeout_s", 20.0))),
                      min_score=float(getattr(settings, "jev_min_score", 0.8)),
                      max_chars=int(getattr(settings, "jev_max_chars", 6000)))
