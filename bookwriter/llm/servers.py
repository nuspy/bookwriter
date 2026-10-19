"""I server personalizzati: voci del registro comune di ``llmswitch`` aggiunte da qui.

Il registro è condiviso con gli altri programmi dell'utente: qui si aggiungono, si modificano e si
tolgono **solo** le voci che questo programma ha creato (marcate con ``origine``), mai quelle degli
altri. Una voce è un server OpenAI-compatibile — un router, un ``llama-server``, un servizio nel
cloud che parla la stessa lingua — con il suo indirizzo, la sua chiave facoltativa e il livello di
ragionamento che di solito vuole.

Si usano i tipi di ``llmswitch`` che bookwriter sa interrogare (``api: "openai"``): sceglierne uno
che non sa parlare farebbe fallire ogni richiesta senza spiegare perché. La libreria non si
modifica: tutto passa per la sua interfaccia pubblica (``Registry.add/update/remove/test``).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from llmswitch import TYPES, Registry

#: Il segno che una voce l'ha creata questo programma.
ORIGIN = "bookwriter"

#: I tipi che si possono scegliere per un server personalizzato: i soli OpenAI-compatibili. Il
#: primo è il tipo generico (con o senza chiave), il secondo un ``llama-server`` locale.
CUSTOM_TYPES: tuple[str, ...] = ("openai", "llamacpp")


def registry() -> Registry:
    """Il registro comune, quello di default (le variabili d'ambiente dei test lo isolano)."""
    return Registry()


def type_choices() -> list[tuple[str, str]]:
    """``(tipo, etichetta)`` per la tendina: l'etichetta di ``llmswitch``, come la scrive lui."""
    return [(k, str(TYPES[k]["label"])) for k in CUSTOM_TYPES if k in TYPES]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "server"


def list_custom(reg: Registry | None = None) -> list[dict[str, Any]]:
    """I server creati da questo programma, nell'ordine in cui stanno nel registro."""
    reg = reg or registry()
    return [dict(p) for p in reg.load().get("providers", [])
            if isinstance(p, dict) and p.get("origine") == ORIGIN]


def get_custom(pid: str, reg: Registry | None = None) -> dict[str, Any] | None:
    return next((p for p in list_custom(reg) if p.get("id") == pid), None)


def _mark(reg: Registry, pid: str) -> None:
    data = reg.load()
    for p in data["providers"]:
        if p.get("id") == pid:
            p["origine"] = ORIGIN
    reg.save(data)


def validate(name: str, base_url: str, tipo: str) -> str:
    """Un messaggio in parole se i dati non vanno; stringa vuota se vanno."""
    if not name.strip():
        return "Dai un nome al server."
    if not re.match(r"^https?://\S+$", base_url.strip()):
        return "L'indirizzo deve cominciare con http:// o https:// (es. http://127.0.0.1:7232/v1)."
    if tipo not in CUSTOM_TYPES:
        return "Tipo di API non supportato."
    return ""


def _set_field(reg: Registry, pid: str, campo: str, valore: Any) -> None:
    """Scrive un campo nostro nella voce (la libreria non lo conosce, ma lo conserva)."""
    data = reg.load()
    for p in data["providers"]:
        if p.get("id") == pid:
            p[campo] = valore
    reg.save(data)


def add_custom(name: str, base_url: str, tipo: str = "openai", api_key: str = "",
               reasoning_level: str = "inherit", structured_output: str = "json_schema",
               reg: Registry | None = None) -> dict[str, Any]:
    """Aggiunge un server al registro. Solleva ``ValueError`` se i dati non vanno."""
    errore = validate(name, base_url, tipo)
    if errore:
        raise ValueError(errore)
    reg = reg or registry()
    esistenti = {p.get("id") for p in reg.load().get("providers", [])}
    base_id = f"bw-{_slug(name)}"
    pid, n = base_id, 2
    while pid in esistenti:
        pid, n = f"{base_id}-{n}", n + 1
    voce = reg.add({"id": pid, "type": tipo, "name": name.strip(),
                    "base_url": base_url.strip().rstrip("/"), "api_key": api_key.strip(),
                    "reasoning_level": reasoning_level})
    _mark(reg, pid)
    _set_field(reg, pid, "structured_output", structured_output)
    return {**voce, "origine": ORIGIN, "structured_output": structured_output}


def update_custom(pid: str, *, name: str, base_url: str, api_key: str,
                  reasoning_level: str, structured_output: str | None = None,
                  reg: Registry | None = None) -> dict[str, Any]:
    reg = reg or registry()
    if get_custom(pid, reg) is None:
        raise KeyError(pid)
    errore = validate(name, base_url, "openai")
    if errore:
        raise ValueError(errore)
    voce = reg.update(pid, {"name": name, "base_url": base_url.rstrip("/"), "api_key": api_key,
                            "reasoning_level": reasoning_level})
    if structured_output is not None:
        _set_field(reg, pid, "structured_output", structured_output)
    return voce


def remove_custom(pid: str, reg: Registry | None = None) -> None:
    """Toglie un server. Mai una voce di altri: se non l'ha creata questo programma, no."""
    reg = reg or registry()
    if get_custom(pid, reg) is None:
        raise KeyError(pid)
    reg.remove(pid)


def try_server(base_url: str, tipo: str = "openai", api_key: str = "") -> dict[str, Any]:
    """«Prova»: interroga ``/models`` del server e dice cosa ha trovato.

    Si fa su un registro **temporaneo**: provare un indirizzo non deve lasciare niente nel registro
    vero. Ritorna ``{"ok": bool, "models": int, "sample": [id…], "error": str}``.
    """
    errore = validate("prova", base_url, tipo)
    if errore:
        return {"ok": False, "error": errore, "models": 0, "sample": []}
    with tempfile.TemporaryDirectory(prefix="bw_prova_") as cartella:
        reg = Registry(path=Path(cartella) / "prova.json")
        pid = reg.add({"type": tipo, "name": "prova", "base_url": base_url.strip().rstrip("/"),
                       "api_key": api_key.strip()})["id"]
        esito = reg.test(pid)
    if esito.get("ok"):
        return {"ok": True, "models": int(esito.get("models") or 0),
                "sample": list(esito.get("sample") or []), "error": ""}
    return {"ok": False, "models": 0, "sample": [], "error": str(esito.get("error") or "")}
