"""Gestione del servizio browser esterno (TRAWL / FlareSolverr).

Avvia TRAWL (in tools/trawl) su richiesta o all'avvio dell'applicazione principale,
esegue le chiamate HTTP compatibili con l'API /v1 di FlareSolverr per le pagine
bloccate (Interfaccia A di docs/browser_integration.md), e chiude pulito il processo
e l'albero di processi Camoufox allo spegnimento.
"""

from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_process: subprocess.Popen[Any] | None = None
_managed_by_us: bool = False


def find_bun() -> str | None:
    """Cerca l'eseguibile di Bun nel sistema."""
    # 1. ~/.bun/bin (priorità agli aggiornamenti recenti / versione utente)
    user_home = Path.home()
    home_bun = user_home / ".bun" / "bin" / ("bun.exe" if os.name == "nt" else "bun")
    if home_bun.is_file():
        return str(home_bun)

    # 2. Nel PATH
    which = shutil.which("bun")
    if which:
        return which

    # 3. Percorsi WinGet su Windows
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        winget_bun = Path(localappdata) / "Microsoft" / "WinGet" / "Packages"
        if winget_bun.exists():
            for p in winget_bun.glob("*Bun*/**/bun.exe"):
                if p.is_file():
                    return str(p)

    return None



#: Perché l'ultimo avvio non è riuscito (vuoto se è riuscito): la finestra lo dice.
last_error: str = ""


def _candidates() -> list[Path]:
    """Dove può stare TRAWL: la variabile ``BOOKWRITER_TRAWL_DIR``, accanto ai sorgenti, accanto
    all'eseguibile compilato e risalendo da lì (``dist/bookwriter/bookwriter.exe`` sta due
    cartelle sotto il progetto, dove c'è ``tools/trawl``)."""
    import sys

    fuori: list[Path] = []
    if env := os.environ.get("BOOKWRITER_TRAWL_DIR", "").strip():
        fuori.append(Path(env))
    for base in (Path(__file__).resolve(), Path(sys.executable).resolve()):
        for genitore in list(base.parents)[:5]:
            fuori.append(genitore / "tools" / "trawl")
    return fuori


def trawl_dir() -> Path:
    """La cartella di TRAWL: la prima delle candidate che contiene il servizio."""
    for c in _candidates():
        if (c / "apps" / "api" / "src" / "index.ts").is_file():
            return c
    return Path(__file__).resolve().parents[2] / "tools" / "trawl"


def is_trawl_running(url: str = "http://127.0.0.1:8191") -> bool:
    """Verifica se il servizio TRAWL è in ascolto e risponde."""
    test_url = url.rstrip("/")
    if test_url.endswith("/v1"):
        test_url = test_url[:-3]
    try:
        resp = httpx.get(f"{test_url}/", timeout=1.0)
        return resp.status_code == 200 and ("TRAWL" in resp.text or "ok" in resp.text.lower())
    except Exception:
        return False


def start_trawl(port: int = 8191, wait_ready: bool = True, timeout_s: float = 25.0) -> bool:
    """Avvia TRAWL in background come microservizio locale.

    Se è già in esecuzione (avviato dall'utente separatamente o da una precedente sessione),
    lo riutilizza senza toccarlo.
    """
    global _process, _managed_by_us, last_error

    target_url = f"http://127.0.0.1:{port}"
    last_error = ""
    if is_trawl_running(target_url):
        return True

    bun_exe = find_bun()
    if not bun_exe:
        last_error = "Bun non è installato (winget install Oven-sh.Bun)"
        logger.warning("Bun non trovato nel sistema: impossibile avviare automaticamente TRAWL.")
        return False

    base_dir = trawl_dir()
    api_dir = base_dir / "apps" / "api"
    entry_point = api_dir / "src" / "index.ts"
    if not entry_point.is_file():
        last_error = f"TRAWL non trovato (cercato in {base_dir}): esegui update_trawl.ps1"
        logger.warning("File di avvio di TRAWL non trovato: %s", entry_point)
        return False

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["SESSION_CACHE_DRIVER"] = "memory"
    env["BROWSER_POOL_SIZE"] = "1"
    env["BROWSER_HEADFUL_POOL_SIZE"] = "0"
    env["SCRAPE_MIN_TIER"] = "1"
    env["MCP_ENABLED"] = "false"
    env["MITM_ENABLED"] = "false"

    log_file = base_dir / "trawl.log"

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        out_f = open(log_file, "a", encoding="utf-8", errors="replace")
        _process = subprocess.Popen(
            [bun_exe, "--cwd", str(api_dir), "src/index.ts"],
            cwd=str(base_dir),
            env=env,
            stdout=out_f,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        _managed_by_us = True
    except Exception as exc:
        last_error = f"avvio non riuscito: {exc}"
        logger.error("Errore durante l'avvio di TRAWL: %s", exc)
        return False

    if not wait_ready:
        return True

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if is_trawl_running(target_url):
            logger.info("TRAWL avviato e pronto su %s", target_url)
            return True
        if _process.poll() is not None:
            last_error = f"TRAWL si è chiuso subito (codice {_process.returncode}): vedi trawl.log"
            logger.error("Il processo TRAWL è terminato prematuramente con codice %s", _process.returncode)
            _managed_by_us = False
            _process = None
            return False
        time.sleep(0.5)

    last_error = f"TRAWL non ha risposto entro {timeout_s:.0f} secondi"
    logger.warning("Timeout in attesa dell'avvio di TRAWL dopo %.1f secondi", timeout_s)
    return False


def stop_trawl() -> None:
    """Spegne TRAWL e tutti i suoi processi figli (Camoufox) se è stato avviato da noi."""
    global _process, _managed_by_us

    if not _managed_by_us or _process is None:
        return

    pid = _process.pid
    _managed_by_us = False

    if os.name == "nt":
        try:
            # Termina l'intero albero di processi (Bun + Camoufox/Firefox)
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        except Exception:
            try:
                _process.kill()
            except Exception:
                pass
    else:
        try:
            _process.terminate()
            _process.wait(timeout=5.0)
        except Exception:
            try:
                _process.kill()
            except Exception:
                pass

    _process = None
    logger.info("TRAWL arrestato pulito.")


atexit.register(stop_trawl)


def fetch_via_flaresolverr(url: str, endpoint: str = "http://127.0.0.1:8191/v1",
                           timeout_s: float = 60.0) -> tuple[int, str] | None:
    """Richiede una pagina tramite l'API /v1 compatibile con FlareSolverr (Interfaccia A).

    Restituisce (status_code, html) se la soluzione ha avuto successo, altrimenti None.
    """
    if not endpoint:
        return None

    # Assicura che l'endpoint punti a /v1
    clean_endpoint = endpoint.rstrip("/")
    if not clean_endpoint.endswith("/v1"):
        clean_endpoint = f"{clean_endpoint}/v1"

    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": int(timeout_s * 1000),
    }

    try:
        # Aggiunge 5 secondi di margine al client HTTP rispetto al timeout interno del solver
        with httpx.Client(timeout=timeout_s + 5.0) as client:
            resp = client.post(clean_endpoint, json=payload)
            if resp.status_code != 200:
                logger.warning("FlareSolverr ha risposto con HTTP %d: %s", resp.status_code, resp.text[:200])
                return None

            data = resp.json()
            if data.get("status") != "ok":
                logger.warning("FlareSolverr status non ok: %s", data.get("message", ""))
                return None

            solution = data.get("solution") or {}
            status_code = solution.get("status", 0)
            html = solution.get("response", "")

            if 200 <= status_code < 300 and html:
                return status_code, html

            logger.warning("FlareSolverr status soluzione %d o html vuoto", status_code)
            return None
    except Exception as exc:
        logger.warning("Errore durante la chiamata a FlareSolverr (%s): %s", clean_endpoint, exc)
        return None


def update_trawl() -> dict[str, Any]:
    """Aggiorna il microservizio TRAWL dal repository originale GitHub.

    - Ferma i processi attivi di TRAWL/Camoufox.
    - Se tools/trawl non esiste, clona il repository da https://github.com/germondai/trawl.git.
    - Altrimenti esegue git fetch e git pull --rebase (con fallback a git pull).
    - Esegue bun install per aggiornare le dipendenze.
    - Assicura la presenza del file .env con la configurazione in-memory.
    - Restituisce un dizionario con esito, commit, data e percorso.
    """
    stop_trawl()

    if not shutil.which("git"):
        raise RuntimeError("Git non trovato nel sistema. Installa Git per continuare.")

    bun_exe = find_bun()
    if not bun_exe:
        raise RuntimeError("Bun non trovato nel sistema. Installalo con 'winget install Oven-sh.Bun' o da https://bun.sh")

    target = trawl_dir()
    repo_url = "https://github.com/germondai/trawl.git"

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(["git", "clone", "--depth", "1", repo_url, str(target)], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Errore durante git clone: {res.stderr}")
    else:
        res = subprocess.run(["git", "pull", "--rebase"], cwd=str(target), capture_output=True, text=True)
        if res.returncode != 0:
            res = subprocess.run(["git", "pull"], cwd=str(target), capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Errore durante git pull: {res.stderr}")

    res = subprocess.run([bun_exe, "install"], cwd=str(target), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Errore durante bun install: {res.stderr}")

    env_file = target / ".env"
    if not env_file.exists():
        env_content = (
            "PORT=8191\n"
            "SESSION_CACHE_DRIVER=memory\n"
            "BROWSER_POOL_SIZE=1\n"
            "BROWSER_HEADFUL_POOL_SIZE=0\n"
            "SCRAPE_MIN_TIER=1\n"
            "MCP_ENABLED=false\n"
            "MITM_ENABLED=false\n"
        )
        env_file.write_text(env_content, encoding="utf-8")

    commit = ""
    date = ""
    try:
        c_res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(target), capture_output=True, text=True)
        if c_res.returncode == 0:
            commit = c_res.stdout.strip()
        d_res = subprocess.run(["git", "log", "-1", "--format=%cd", "--date=short"], cwd=str(target), capture_output=True, text=True)
        if d_res.returncode == 0:
            date = d_res.stdout.strip()
    except Exception:
        pass

    return {
        "status": "ok",
        "commit": commit,
        "date": date,
        "path": str(target),
    }

