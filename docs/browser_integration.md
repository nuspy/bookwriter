# Pagine bloccate: collegare uno strumento esterno

> **Stato:** implementata e integrata nel codice (incluso il microservizio TRAWL in `tools/trawl`).

Durante la ricerca alcune pagine non si possono leggere: il sito rifiuta la richiesta, oppure mostra una verifica
(reCAPTCHA, hCaptcha, Cloudflare Turnstile, «Just a moment…», DDoS-Guard) invece del contenuto. bookwriter **non
risolve queste verifiche** e non contiene un browser. Espone invece due punti di aggancio: consegna le pagine che non
è riuscito a leggere e riprende ciò che uno strumento esterno, scelto e gestito da te, gli restituisce. Lo strumento
esterno è una tua scelta e una tua responsabilità.

Le pagine restituite seguono lo stesso percorso di quelle scaricate direttamente: pulizia del testo, giudizio di
pertinenza, estrazione del materiale. La ricerca non si ferma mai per una pagina bloccata: va avanti con il resto.

## Tipi di blocco riconosciuti

Ogni pagina non leggibile viene registrata nei dati della ricerca con il suo motivo:

| Motivo (`reason`) | Quando |
|---|---|
| `captcha` | la pagina contiene una verifica reCAPTCHA, hCaptcha o Turnstile |
| `challenge` | pagina d'attesa anti-bot («Just a moment…», «Checking your browser», DDoS-Guard) |
| `http_403`, `http_429`, `http_503`, … | il sito ha rifiutato la richiesta con quel codice |
| `cookie_wall` | al posto del contenuto c'è solo il consenso ai cookie |
| `login_wall` | al posto del contenuto c'è un muro di accesso **o** una pagina che richiede JavaScript (il lettore di pagine non le distingue: è un solo motivo) |

Le pagine **non pertinenti** e quelle dei **domini esclusi** (YouTube, Amazon e quelli che aggiungi in Impostazioni)
non entrano nella coda: non sono bloccate, sono scartate.

## Interfaccia A — servizio HTTP compatibile con l'API «/v1» di FlareSolverr

È il formato che parlano molti strumenti di questo tipo. Si attiva scrivendo l'indirizzo in **Impostazioni →
Ricerca → Servizio esterno per le pagine bloccate** (chiave `research.external_fetcher_url` in `config.toml`). Vuoto =
spento.

Per ogni pagina bloccata bookwriter manda:

```http
POST http://127.0.0.1:8191/v1
Content-Type: application/json

{"cmd": "request.get", "url": "https://esempio.org/articolo", "maxTimeout": 60000}
```

e si aspetta una risposta come questa:

```json
{
  "status": "ok",
  "solution": {
    "url": "https://esempio.org/articolo",
    "status": 200,
    "response": "<!DOCTYPE html><html> … </html>"
  }
}
```

- bookwriter legge l'HTML da `solution.response`; `solution.status` diverso da 2xx o `status` diverso da `"ok"` vale
  come fallimento.
- Il tempo massimo per pagina (`maxTimeout`) si imposta in **Impostazioni → Ricerca → Tempo massimo per pagina**
  (chiave `research.external_fetcher_timeout_s`, predefinito 60 s).
- Se il servizio non risponde, risponde con un errore o restituisce ancora una pagina di verifica, la pagina resta
  **bloccata** con il motivo e passa alla coda su cartella (interfaccia B). La ricerca non si ferma.

## Interfaccia B — coda su cartella

Funziona sempre, anche senza servizio esterno: è adatta a strumenti che lavorano per conto loro, in un altro momento
o su un'altra macchina.

### Uscita: la coda

Nella cartella del libro, `research/bloccate/coda.jsonl` contiene una riga JSON per ogni pagina bloccata (nessun
doppione):

```json
{"id": "c01p2-4f9a1c", "url": "https://esempio.org/articolo", "chapter": 1, "point": 2, "query": "ayahuasca ceremony icaros smoke sensory", "reason": "captcha", "status": "in_coda", "queued_at": "2026-10-19T03:40:12"}
```

| Campo | Significato |
|---|---|
| `id` | identificativo stabile della pagina: è il nome del file da restituire |
| `url` | l'indirizzo da aprire |
| `chapter`, `point` | il capitolo e il punto per cui la pagina era stata trovata |
| `query` | l'interrogazione che l'ha trovata |
| `reason` | il tipo di blocco (vedi la tabella sopra) |
| `status` | `in_coda` (da fare) o `ricevuta` (importata) |
| `queued_at` | quando è entrata in coda |

### Ricezione: la cartella `ricevute`

Il tuo strumento mette il risultato in `research/bloccate/ricevute/`, **un file per pagina**, chiamato con l'`id` della
riga e una di queste estensioni:

| File | Contenuto |
|---|---|
| `<id>.html` (o `.htm`) | la pagina com'è stata scaricata (HTML completo) |
| `<id>.txt` o `<id>.md` | solo il testo della pagina |
| `<id>.pdf` | il documento PDF (il testo si estrae come per i PDF che importi nel progetto) |

Esempio: per la riga sopra, `research/bloccate/ricevute/c01p2-4f9a1c.html`.

### Importazione

- **Dalla GUI:** scheda **Ricerca** → sottoscheda **Pagine bloccate** → **Importa le pagine ricevute**. Lì trovi
  anche il riepilogo delle pagine bloccate per motivo e il pulsante **Apri la cartella della coda**.
- **Recuperarle dal servizio esterno:** nella stessa sottoscheda, **Recupera le pagine in coda** (vedi sotto).
- **Automatica:** quando riprendi la ricerca, le pagine ricevute vengono importate prima di continuare.
- **Da riga di comando:**

  ```powershell
  python -m bookwriter.cli forge research blocked list      # le pagine in coda, con motivo e stato
  python -m bookwriter.cli forge research blocked export    # riscrive coda.jsonl
  python -m bookwriter.cli forge research blocked import    # importa ciò che c'è in ricevute/
  python -m bookwriter.cli forge research blocked fetch     # le chiede al servizio esterno e le importa
  ```

#### Recuperare le pagine in coda (`blocked fetch`, pulsante **Recupera le pagine in coda**)

Per ogni riga `in_coda` di `coda.jsonl` chiede la pagina al servizio esterno (interfaccia A: stessa richiesta, con
`research.external_fetcher_url` e il tempo massimo `external_fetcher_timeout_s`). Serve quindi l'indirizzo del
servizio: se è vuoto il comando dice «Imposta l'indirizzo del servizio esterno in Impostazioni → Ricerca», non fa
niente ed esce con codice 1 (la GUI mostra lo stesso avviso). Se l'indirizzo è locale (`127.0.0.1`) e l'avvio
automatico è acceso, avvia il servizio e aspetta che risponda, come fa la ricerca.

- Se la pagina ottenuta si legge, viene salvata come `research/bloccate/ricevute/<id>.html` e importata **con
  l'importazione di sempre** (pulizia, giudizio di pertinenza, estrazione nel punto giusto, riga a `ricevuta`).
- Se il servizio non risponde, risponde con un errore o restituisce ancora una verifica, la riga resta `in_coda`: nel
  secondo caso il suo `reason` si aggiorna al tipo di verifica che la pagina mostra (per esempio `challenge`).
- Una pagina che ha già un file in `ricevute/` non si richiede: la importa l'importazione. Nessun errore ferma il resto.
- Riepilogo finale: «Recuperate N di M. Ancora bloccate: … (per motivo). Non pertinenti: … Senza risposta dal
  servizio: …». «Interrompi» ferma il recupero (nella GUI).

Una pagina importata prende il posto della registrazione «illeggibile» di quell'indirizzo nel punto e segue la
pipeline normale (pulizia, giudizio di pertinenza, estrazione); la sua riga passa a `ricevuta`. I file restano nella
cartella, e importare di nuovo non rifà niente per le pagine già importate.

L'importazione riporta un esito per ogni file:

| Esito | Significato |
|---|---|
| `ok` | importata |
| `non_riconosciuta` | il nome non corrisponde a nessun `id` in coda, o l'estensione non è fra quelle sopra: il file resta e viene segnalato |
| `non_leggibile` | il file contiene ancora una verifica o troppo poco testo (meno di `min_page_chars`): la riga resta `in_coda` |
| `vuoto` | il file è vuoto: la riga resta `in_coda` |

Se la pagina importata non è pertinente secondo il modello, resta registrata come *non pertinente* e non si usa.

## Cosa non fa bookwriter

- Non risolve CAPTCHA o altre verifiche e non include né consiglia strumenti per farlo.
- Non apre finestre di browser e non chiede interventi durante la ricerca.
- Non manda le pagine bloccate a servizi diversi da quello che imposti tu.
