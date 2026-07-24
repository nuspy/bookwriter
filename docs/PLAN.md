# bookwriter — Piano di progetto dettagliato

> Applicazione **locale** che trasforma frammenti sparsi (note, appunti, documenti raccolti
> nel tempo senza ordine) in un **libro** o in un **archivio ragionato**, usando un LLM locale
> servito da **LM Studio**. L'organizzazione dei contenuti e lo **stile di scrittura** emergono
> da un dialogo conversazionale con l'utente. Output finale in **DOCX/PDF** (+ archivio wiki).

---

## 1. Principi architetturali

1. **Il nodo è l'unità atomica, non il capitolo.** Un frammento di idea vive nella *memoria*
   del progetto. Non "appartiene" a un capitolo: capitoli e ordini sono *percorsi diversi sullo
   stesso grafo di nodi*. Dagli stessi nodi si generano libri diversi (tematico, cronologico,
   narrativo, …).
2. **Decisione separata dalla generazione.** La conversazione produce prima un **outline**
   strutturato (economico, iterativo); la prosa si genera solo dopo l'approvazione (costoso).
3. **Memoria ibrida.** Vettori (RAG semantico) **+** grafo (relazioni esplicite) sulla stessa
   collezione di nodi. Il "wiki container" è la *vista leggibile* del grafo.
4. **Tutto pluggabile.** Ordinamenti e stili sono strategie/profili estensibili, definibili anche
   a runtime dall'utente o dall'AI durante il dialogo — mai un elenco chiuso nel codice.
5. **Multi-progetto e ripartibile.** Ogni progetto è isolato; ogni fase pesante è a checkpoint,
   riavviabile, e cache-abile (scala nell'ordine delle migliaia di nodi).
6. **Tracciabilità.** Ogni frase generata risale ai nodi sorgente → utile per l'uso "archivio".

---

## 2. Modalità operative

L'app supporta **quattro flussi** che condividono memoria, motore di stile, motore di interazione
ed export:

- **A — Compose (frammenti → libro/archivio):** tanti frammenti sparsi → memoria → **interazione
  per definire e completare i contenuti** (§7.6) → organizzazione → outline dialogico → resa. La
  resa può essere **verbatim** (riordino degli spezzoni originali senza riscrittura) o **narrativa**
  (prosa riscritta nello stile scelto) — vedi §8.0.
- **B — Rewrite (libro → libro, nuovo stile):** un documento grande → chunking deterministico →
  **riscrittura chunk-per-chunk** in un nuovo stile, pipeline deterministica con uniformità. §8.5.
- **C — Translate (libro → libro, altra lingua):** stesso motore deterministico di B, ma
  **traduzione** che mantiene lo stile dell'autore e la coerenza terminologica. §8.6.
- **D — Authoring interattivo (dialogo → libro):** partendo da poco o nulla, l'LLM **crea il
  contenuto conversando** (domande, proposte, suggerimenti) fino alla stesura completa. §8.7.

**Motore trasversale di interazione / co-scrittura (§7.6):** è il cuore condiviso da **A** e **D**
(e dalla generazione di archivi). Non serve solo a scegliere ordinamento e stile, ma a **definire e
completare i contenuti**: analizza la copertura della memoria, individua lacune e parti deboli, fa
domande mirate, propone aggiunte e integra le risposte dell'utente come nuovi nodi. In **D** opera
"da vuoto" (autore-guida); in **A/archivio** opera su una memoria esistente (completa e raffina).

### Pipeline logica (modalità A — Compose)

```
frammenti sparsi ─► ingestione ─► atomizzazione ─► MEMORIA ─► organizzazione ─► outline ─► stesura ─► export
  (txt/md/pdf/docx)   (loader)     (nodi-idea+meta)  (grafo+     (clustering/    (dialogo)  (prosa      (docx/pdf
                                                     vettori)     tempo/arco)     +stile)    +stile)      +wiki)
```

Ogni stadio è un modulo indipendente con I/O su DB, così ognuno è testabile e riavviabile da solo.

---

## 3. Stack tecnologico

| Ambito | Scelta | Note |
|---|---|---|
| Linguaggio | **Python 3.11+** | |
| LLM | **LM Studio** su `http://localhost:1234/v1` | API OpenAI-compatibile → client `openai` puntato lì. Un modello *chat/reasoning* + un modello *embedding*. |
| TUI | **Textual** (app interattiva a pannelli) + **Typer** (comandi da script) + **Rich** | Chat + editing outline + navigazione wiki in terminale. |
| Vettori | **LanceDB** | Embedded (file), niente server, scala a migliaia+ senza problemi. |
| Grafo/metadati | **SQLite** (via SQLModel/SQLAlchemy) + **networkx** | Nodi, archi, documenti, outline, profili di stile. |
| Clustering | **scikit-learn** + **hdbscan** (+ community detection su networkx / leiden) | Ordinamento tematico. |
| Loader documenti | **PyMuPDF** (pdf), **python-docx** (docx), md/txt nativi | Estensibile. |
| Schemi/validazione | **pydantic** | Structured output dall'LLM + integrità dati. |
| Template & export | **Jinja2** + **Pandoc** (esterno) → DOCX; **Typst** o LaTeX → PDF | Un sorgente Markdown → più formati. |
| Concorrenza | `asyncio` + semaforo | Batching atomizzazione/embedding. |

**Riuso ambiente esistente (opzionale, da valutare in Fase 1):** le skill locali `graphify`
(input → knowledge graph → community) e `wiki-import` (LLM Wiki locale per progetti, RAG,
import PDF/DOCX) coprono pezzi di ingestione/grafo; `bookwriter` può orchestrarle invece di
riscrivere tutto. Decisione presa a valle di uno spike tecnico.

---

## 4. Sistema di memorizzazione (il cuore)

### 4.1 Layout su disco (multi-progetto)

```
<WORKSPACE>/                          # default: ~/.bookwriter  (configurabile)
├── registry.db                       # SQLite: elenco progetti, stili globali, config
├── styles/                           # libreria di StyleProfile globali riusabili tra progetti
│   └── <style>.json
└── projects/
    └── <project-slug>/
        ├── project.db                # SQLite: nodi(meta), archi, documenti, outline, stili locali, job/checkpoint
        ├── vectors.lance/            # LanceDB: embedding dei nodi
        ├── sources/                  # copia/riferimento dei documenti importati
        ├── exports/                  # docx / pdf / wiki html generati
        └── config.toml               # modelli LLM scelti, preferenze del progetto
```

Motivazione: DB per-progetto = isolamento, backup, cancellazione banali; `registry.db` tiene solo
l'indice dei progetti e la **libreria di stili globale** (uno stile "autore X" si definisce una
volta e si riusa ovunque).

### 4.2 Schema dati (SQLite + LanceDB)

**Document**
`id · project_id · path · format · sha256 · title · imported_at · char_count · meta(json)`

**Node** (idea atomica) — metadati in SQLite, `embedding` in LanceDB (join per `id`)
`id · project_id · document_id · span_start · span_end · raw_text · summary ·`
`type(idea|fatto|citazione|aneddoto|argomento|domanda) · themes[] · entities[] ·`
`event_date? · date_confidence · salience(0-1) · status · created_at`

**Edge** (relazione tra nodi) — è ciò che abilita i diversi ordinamenti
`id · project_id · src_node · dst_node ·`
`type(thematic|temporal|causal|elaborates|contradicts|example_of|references) ·`
`weight(0-1) · rationale · source(llm|manual)`

**StyleProfile** → vedi §6.
**Outline / Chapter / Section / SectionNodeRef** → vedi §7.
**Job** (checkpoint per fasi pesanti) `id · kind · state · progress · payload(json) · updated_at`
→ rende atomizzazione/embedding/relation-mining **riavviabili** dopo interruzione.

### 4.2bis Ingestione di file singoli **e di intere directory**

L'inserimento deve essere comodo: non solo un file per volta, ma **cartelle intere** (anche annidate).

- **Scansione ricorsiva** di una o più directory, con `--recursive` e filtri
  `--include/--exclude` (glob) per estensione/pattern; formati supportati riconosciuti
  automaticamente (txt, md, pdf, docx, epub, …), gli altri saltati con log.
- **Dedup** per `sha256`: file già importati (stesso contenuto) vengono ignorati → re-ingestione di
  una cartella è idempotente e incrementale (aggiunge solo i nuovi/modificati).
- **Coda a checkpoint**: ogni file è un `Job`; l'import di migliaia di file è ripartibile, con
  barra di avanzamento, riepilogo finale (importati / saltati / falliti) e log degli errori per file.
- **Metadati preservati**: percorso relativo, cartella d'origine e (se presenti) titolo/autore dei
  documenti, utili poi come tag/temi.
- **Watch opzionale** (fase avanzata): `--watch` per importare in automatico i nuovi file che
  compaiono in una cartella.

Ogni documento importato attraversa poi il **chunking deterministico** (§4.3) e — in modalità A —
l'atomizzazione in nodi.

### 4.3 Chunking deterministico di documenti grandissimi (un libro intero)

Un libro non entra nella finestra di contesto: va **spezzato in chunk gestibili dall'LLM**. Lo
split deve essere **deterministico** (nessuna casualità: stesso file → stessi chunk), *structure-aware*
e reversibile:

1. **Parsing strutturale** — dal documento (pdf/docx/epub/md) si ricava un albero
   `libro → parti → capitoli → sezioni → paragrafi` sfruttando titoli/stili/heading. È la spina
   dorsale che preserva i confini naturali.
2. **Chunking a budget di token** — si accorpano paragrafi finché non si raggiunge un budget
   (es. ~1–2k token, parametrico sul modello caricato), **senza mai spezzare una frase** e
   preferendo tagliare ai confini di paragrafo/sezione. Overlap configurabile (es. 1–2 frasi)
   per la continuità.
3. **Indicizzazione posizionale** — ogni chunk conserva `book_pos` (ordine assoluto),
   `chapter_ref`, `span_start/end`, `prev_id/next_id`. Così il libro è **ricomponibile alla lettera**.
4. **Determinismo & idempotenza** — l'algoritmo di split è puro codice (niente LLM), quindi
   ripetibile; ogni chunk è cache-ato per `sha256(testo)`; la riesecuzione riprende dai `Job`.

I chunk alimentano **sia** la modalità A (ogni chunk viene poi *atomizzato* in nodi-idea) **sia**
la modalità B (ogni chunk viene *riscritto*, senza atomizzazione). La tabella dei chunk è
condivisa; cambia solo cosa ci fai sopra.

**Chunk** (SQLite)
`id · project_id · document_id · book_pos · chapter_ref · span_start · span_end ·`
`text · token_count · prev_id · next_id · sha256`

### 4.4 Costruzione del grafo a scala (evitare O(n²))

Con migliaia di nodi non si confrontano tutte le coppie. Per ogni nodo:
1. recupera i **top-k** vicini semantici da LanceDB (k≈10);
2. l'LLM classifica solo *quelle* coppie candidate → tipo di arco + peso;
3. gli archi temporali derivano da `event_date`; quelli tematici anche dai cluster.

Batching + `Job`/checkpoint + cache per `sha256(contenuto)` → resa idempotente e ripartibile.

---

## 5. Organizzazione: ordinamenti come strategie pluggabili

Interfaccia comune (`OrderingStrategy`): input = insieme di nodi + grafo; output = **struttura ad
albero** capitoli→sezioni→`node_refs[]` (bozza di outline, prima del dialogo).

| Strategia | Come | Note |
|---|---|---|
| **Tematica** | clustering embeddings (HDBSCAN) + community detection sul grafo → 1 cluster ≈ 1 capitolo; sotto-clustering → sezioni | prima da implementare |
| **Cronologica** | ordina per `event_date` (estratta/inferita); raggruppa per epoche/fasi | usa `date_confidence` |
| **Narrativa** | l'LLM costruisce un arco (setup→tensione→risoluzione) scegliendo l'ordine dei cluster e le transizioni | l'unica che ragiona sull'insieme |
| **Definita dall'utente / dall'AI** | descrizione in linguaggio naturale ("per luoghi", "per personaggio", "domanda→risposta"…) → l'LLM la traduce in criteri di raggruppamento/ordinamento eseguibili | **estensibile a runtime**: nuova strategia = nuovo prompt+parametri, nessuna modifica al codice |

Le strategie sono registrate in un registry; l'utente può salvarne di nuove nel progetto.

---

## 6. Motore di stile (feature di primo livello)

Lo **stile di scrittura** del libro/archivio è scelto dall'utente. **L'LLM deve sempre chiedere
questi dettagli** prima della stesura. Tre modalità di origine, combinabili:

- **A. Estratto dai documenti** — si analizza un campione dei testi sorgente e se ne deriva lo stile.
- **B. Definito in astratto via dialogo** — l'utente descrive ("saggistico ma caldo, frasi brevi,
  ironia leggera"); l'LLM interroga finché lo `StyleProfile` è completo.
- **C. Imitazione di autori** — uno o più autori famosi. Con più autori si definiscono **pesi** e
  **ambiti** (quale dimensione stilistica prende da chi).

### 6.1 Dimensioni stilistiche (la "StyleCard")

Lo stile è scomposto in dimensioni misurabili/controllabili — così è mescolabile e verificabile:

| Dimensione | Cosa cattura |
|---|---|
| **Sintassi** | lunghezza media frasi, para/ipotassi, subordinate, incisi, frasi nominali |
| **Punteggiatura** | uso di `;` `:` `—`, parentesi, ritmo dei punti |
| **Lessico** | registro (aulico↔colloquiale), ricchezza (type-token ratio), tecnicismi, arcaismi |
| **Densità figurativa** | metafore, similitudini, allitterazioni, anafore, parallelismi |
| **Ritmo/prosodia** | cadenza, variazione di lunghezza, respiro del paragrafo |
| **Voce & POV** | persona (I/III), tempo verbale, distanza narrativa, discorso indiretto libero |
| **Tono** | ironico / solenne / intimo / saggistico / lirico |
| **Coesione** | connettivi, riferimenti anaforici, topic sentence |
| **Struttura paragrafo** | lunghezza, digressioni, aperture/chiusure |

`StyleProfile` (pydantic + JSON):
```
id · name · scope(global|project) · origin(extracted|abstract|authors) ·
dimensions{<dim>: {descrittori testuali + parametri numerici}} ·
exemplars[<passaggi few-shot>] ·
author_blend[ {author, weight, dims:[<quali dimensioni prende da questo autore>]} ] ·
constraints[<vincoli duri: es. "mai gergo tecnico">]
```

### 6.2 Come funziona (tre operazioni)

1. **Extract** — da testo campione (documenti *o* pochi esempi dell'autore forniti dall'utente),
   l'LLM produce una StyleCard strutturata (dimensioni + esemplari).
2. **Blend** — più StyleCard combinate **per-dimensione** secondo i pesi
   (es. *sintassi 70% autore X + 30% autore Y, lessico 100% autore Z*) → StyleCard composita.
3. **Apply** — in stesura la StyleCard diventa un "contratto di stile" nel system prompt +
   2-3 esemplari few-shot; poi un **passaggio critico** (style-editor) riscrive la bozza per
   conformarla, con loop finché una checklist stilistica è soddisfatta.

> **Nota su diritti/etica:** si imita lo *stile* (pastiche), **non** si riproduce testo protetto
> alla lettera. Si usano *descrittori* dello stile ed esempli originali, non estratti copiati.
> Il fine-tuning/LoRA su un autore è fuori scope MVP (approccio prompt-based sufficiente e locale).

---

## 7. Flusso conversazionale (TUI) e outline

Macchina a stati guidata dal *conversation manager*. L'LLM **chiede sempre** ordinamento e stile.

```
1. RIEPILOGO MEMORIA   → "trovati N nodi, M temi; i 3 principali sono …"
2. SCELTA ORDINAMENTO  → propone tematico/cronologico/narrativo o chiede uno stile su misura
3. SCELTA STILE        → interroga fino a completare lo StyleProfile (estratto/astratto/autori+pesi)
4. BOZZA OUTLINE       → strategia scelta genera capitoli→sezioni→node_refs (JSON)
5. EDITING OUTLINE     → l'utente riordina/unisce/scarta in chat ("sposta X prima", "togli il tema Y")
6. APPROVAZIONE        → congela l'outline
7. STESURA             → sezione per sezione: RAG dei node_refs + contratto di stile + transizioni
8. REVISIONE GLOBALE   → passaggio di coerenza/continuità sull'intero manoscritto
9. EXPORT              → DOCX/PDF (+ wiki archivio)
```

**Outline** (editabile, versionato in `project.db`):
`Outline(id·name·ordering·style_profile_id·status) → Chapter[] → Section[] → SectionNodeRef[]`.
Ogni sezione conosce i nodi che deve "tessere", così la stesura è mirata e tracciabile.

---

## 7.5 BookSpec — target di lunghezza (numero di parole)

L'utente **dichiara quante parole** deve avere il libro (o l'archivio riscritto). È un parametro di
primo livello, impostabile alla creazione del progetto, in dialogo, o da GUI/CLI, e conservato con
l'outline.

`BookSpec` (pydantic, in `project.db`)
`target_words? · tolerance_pct(default 5) · distribution(proportional|equal|manual) ·`
`per_chapter_words{chapter_id: words}?  # solo se distribution=manual`

**Come il target guida la generazione (modalità Compose):**
1. **Ripartizione** del totale sulle sezioni → *budget di parole per sezione*.
   - `proportional` (default): peso della sezione = n° nodi × salienza → più materiale, più spazio.
   - `equal`: parti uguali; `manual`: valori fissati dall'utente per capitolo, poi ripartiti.
2. **Generazione mirata**: ogni sezione riceve nel prompt il proprio budget ("≈ N parole").
3. **Riconciliazione**: si misura il conteggio reale; se fuori tolleranza, un passaggio dedicato
   **espande** (aggiunge dettaglio/nodi correlati) o **comprime** (sintetizza) le sezioni finché il
   totale rientra in `target_words ± tolerance_pct`.

**In modalità Rewrite:** il target può essere assoluto (`--target-words`) **o** un rapporto sul
testo originale (`--ratio`, es. 0.7 = comprimi al 70%, 1.5 = espandi). Diventa un budget per-chunk;
il critic loop verifica anche la lunghezza oltre allo stile.

**Feedback continuo:** GUI e CLI mostrano sempre *obiettivo vs stimato* (es. "50.000 obiettivo ·
48.200 stimate") già in fase di outline, così l'utente calibra la struttura prima di generare.

## 7.6 Motore di interazione / co-scrittura (definire e completare i contenuti)

Layer conversazionale condiviso da **Compose (A)**, **generazione di archivi** e **Authoring (D)**.
Va oltre la scelta di ordinamento/stile: serve a **definire e completare il contenuto**.

Capacità:
- **Analisi di copertura**: sui nodi in memoria individua temi sottili, buchi logici, transizioni
  mancanti, domande senza risposta, affermazioni non supportate.
- **Domande mirate**: interroga l'utente per colmare le lacune ("manca un esempio per il tema X:
  vuoi aggiungerne uno?").
- **Proposte/suggerimenti**: propone nuovi paragrafi, collegamenti tra nodi, riformulazioni.
- **Completamento**: le risposte dell'utente diventano **nuovi nodi** (`source="dialogue"`),
  indicizzati come gli altri → la memoria cresce durante il dialogo.
- **Ciclo**: analizza → chiedi/suggerisci → integra → rianalizza, finché l'utente è soddisfatto o
  la copertura è adeguata al `BookSpec`.

In **D (Authoring)** lo stesso ciclo parte "da vuoto": l'LLM elicita tema, pubblico, obiettivi,
struttura, poi sezione per sezione raccoglie contenuto e redige, fino alla stesura completa.

## 7.7 Memoria degli intenti e continuità tra sessioni

**Tutti gli intenti e le decisioni delle interazioni sono persistiti** in project.db, così il
progetto si riprende in sessioni di lavoro diverse e con sessioni LLM diverse senza perdere il filo.

Entità (project.db):
- **Session** `id · mode · started_at · ended_at?` — una sessione di lavoro.
- **Message** `id · session_id · role(user|assistant) · content · created_at` — traccia del dialogo.
- **Intent** (decisione/intento) `id · kind(ordering|style|scope|audience|goal|content-gap|todo) ·`
  `value(json) · status(open|resolved|dropped) · rationale · created_at · updated_at`.
- **OpenQuestion** `id · text · status(open|answered) · answer? · created_from_session`.

**Ripresa di sessione (project brief):** all'avvio di una nuova sessione il sistema ricostruisce un
"brief" = `BookSpec` + `StyleProfile` attivo + scelta di ordinamento + **intenti/decisioni aperti**
+ **domande aperte** + ultimi messaggi rilevanti, e lo inietta come contesto → l'LLM continua come
se non si fosse mai interrotto. Vale anche per **B/C**: il `ContinuityState` (glossario, decisioni
di stile, avanzamento per chunk) è già persistito come checkpoint e concorre al brief.

## 8. Stesura ed export

### 8.0 Due rese possibili in Compose: **verbatim** vs **narrativa**

Quando l'utente fornisce materiale disordinato può scegliere **come** ottenere il risultato:

- **Compilazione verbatim (riordino senza riscrittura)** — l'LLM **non riscrive** il contenuto:
  raggruppa e ordina gli **spezzoni originali** (nodi/chunk) in **capitoli coerenti per tema o
  argomento**, mantenendo il testo *alla lettera*. Ideale per antologie/archivi ad alta fedeltà.
  Pipeline leggera: memoria → organizzazione (clustering/ordinamento) → assemblaggio dei testi
  originali → export. L'LLM interviene solo per *raggruppare/ordinare* (ed eventuali titoli e
  brevissimi raccordi opzionali), **mai** sul contenuto degli spezzoni. Disponibile presto (subito
  dopo l'organizzazione, senza le fasi di stile/stesura).
- **Stesura narrativa (riscritta)** — l'LLM genera **prosa nuova** che intreccia i nodi nello stile
  scelto (il flusso descritto qui sotto).

La scelta è un parametro dell'outline (`render_mode = verbatim | narrative`), impostabile in dialogo/GUI.

### 8.1 Stesura narrativa ed export

- **Compose**: per ogni sezione → recupera i nodi (RAG), genera prosa nello stile, cura le
  transizioni tra sezioni/capitoli; poi passaggio globale di coerenza (nomi, tempo verbale, ripetizioni).
- **Export**: Markdown assemblato → **Pandoc** → **DOCX**; → **Typst/LaTeX** → **PDF** con template
  curato (frontespizio, indice, note). Metadati libro (titolo, autore, colophon) raccolti in dialogo.
- **Archivio**: export alternativo come **wiki HTML/JSON** navigabile (nodi = pagine, archi = link)
  — la modalità "archivio" invece che "libro".

---

## 8.5 Modalità B — Riscrittura di un libro in un nuovo stile (deterministica)

Caso d'uso: prendere **un libro intero** e riscriverlo in un altro stile, mantenendo **uniformità
narrativa** dall'inizio alla fine. Lo stile è **sempre deciso in dialogo con l'utente** (le tre
origini del §6: estratto / astratto / autori+pesi). La pipeline è **algoritmica e deterministica**:
legge un chunk alla volta, lo riscrive, va avanti — nessuna dipendenza dall'ordine di esecuzione,
riavviabile senza perdere coerenza.

### Pipeline

```
libro ─► parsing strutturale ─► chunk ordinati ─► [ per ogni chunk, in ordine assoluto:
                                                     riscrivi(chunk, StyleContract, ContinuityState) ]
                                                  ─► riassemblaggio ─► revisione globale ─► export
```

1. **Chunking deterministico** (§4.3): il libro diventa una sequenza ordinata di chunk
   (`book_pos`), con confini a frase/paragrafo.
2. **StyleContract**: lo `StyleProfile` scelto in dialogo diventa un contratto di stile fisso,
   identico per tutti i chunk → uniformità.
3. **ContinuityState (memoria di continuità)** — l'ingrediente anti-deriva. È uno stato che
   scorre di chunk in chunk e contiene:
   - **glossario/entità**: come sono rese nomi, luoghi, termini ricorrenti (coerenza lessicale);
   - **decisioni stilistiche fissate**: persona, tempo verbale, registro, rese tipiche;
   - **coda di contesto**: le ultime N frasi *già riscritte* (per transizioni fluide);
   - **riassunto progressivo** del già-riscritto (per tono e riferimenti a lungo raggio).
   Ogni riscrittura riceve `(chunk_corrente, StyleContract, ContinuityState)` e restituisce il
   testo riscritto + eventuali aggiornamenti allo stato. Lo stato è **persistito** (checkpoint) →
   ripresa deterministica dopo interruzione.
4. **Riscrittura chunk-per-chunk, in ordine**: l'LLM riscrive *solo* nel rispetto del contratto e
   della continuità; un **critic loop** per chunk verifica una checklist stilistica (sintassi,
   registro, POV, tempo) e ripete se non conforme.
5. **Riassemblaggio** rispettando `book_pos` → manoscritto completo.
6. **Revisione globale** di coerenza (nomi/timeline/ripetizioni) e **export** DOCX/PDF.

> L'ordine dei chunk è deterministico (`book_pos`); la continuità è portata dallo stato esplicito,
> non dalla finestra di contesto — così un libro di qualunque lunghezza mantiene stile uniforme.

### Preview del primo capitolo (approvazione dello stile *prima* di elaborare tutto)

Prima di riscrivere l'intero libro (operazione lunga/costosa), il sistema **riscrive solo il primo
capitolo** (o un campione di N chunk) e lo mostra all'utente come **anteprima**:

- l'utente confronta *originale ↔ riscritto* fianco a fianco;
- se lo stile non convince, **aggiusta lo `StyleProfile` in dialogo** e rigenera la sola preview
  (loop veloce ed economico);
- quando approva, la **StyleContract viene congelata** e parte la riscrittura dell'intero libro con
  quello stile. La preview già prodotta viene riutilizzata (cache), non rigenerata.

Questo dà controllo, evita sprechi e garantisce che l'uniformità parta da uno stile validato.

---

## 8.6 Modalità C — Traduzione automatica di un libro

Traduce un libro intero in un'altra lingua **riusando lo stesso motore deterministico di B**
(§8.5): chunking → elaborazione chunk-per-chunk in ordine → riassemblaggio → export. Differenze:

- **Obiettivo per chunk**: *tradurre* nella lingua target **mantenendo lo stile** dell'autore
  (lo `StyleProfile` estratto dall'originale o scelto dall'utente).
- **ContinuityState bilingue**: il glossario tiene la resa **coerente** di nomi propri, termini
  tecnici, modi di dire ricorrenti per tutto il libro.
- **Fattore di espansione**: le lingue cambiano lunghezza (es. IT↔EN); si monitora e, se serve,
  si applica un `--ratio`/`--target-words` come in §7.5.
- **Preview del primo capitolo** con confronto **originale ↔ traduzione** prima di elaborare tutto.
- Gli intenti (lingua, glossario, scelte di resa) sono persistiti (§7.7) per riprendere in più sessioni.

## 8.7 Modalità D — Authoring interattivo (dialogo → libro)

Crea un libro **da zero** attraverso il dialogo, usando il motore di interazione (§7.6):

1. **Brief iniziale**: l'LLM elicita tema, tesi, pubblico, obiettivi, tono, `BookSpec` (parole),
   `StyleProfile`.
2. **Struttura proposta**: genera un outline candidato; l'utente lo raffina.
3. **Elicitazione + stesura per sezione**: per ogni sezione fa domande, propone contenuti,
   raccoglie le risposte (→ nuovi nodi `source="dialogue"`) e redige la bozza nello stile scelto.
4. **Avanzamento fino al completamento**: procede sezione per sezione, mostrando *obiettivo vs
   stimato* di parole, finché la bozza è completa.
5. Ogni intento/decisione è salvato nel progetto (§7.7) → l'opera si costruisce in **più sessioni**.

## 9. Interfacce: GUI (primaria) + CLI

### 9.1 GUI (interfaccia primaria)

L'esperienza principale è **grafica**. Il core Python è condiviso: la GUI e la CLI sono due
"pelli" sopra gli stessi moduli (ingest / memory / organize / style / compose / export).

Schermate/pannelli previsti (ogni modalità ha il suo spazio dedicato):
- **Progetti**: crea/apri/switch, con il target di parole (§7.5) e il **brief di ripresa** (§7.7).
- **Ingestione**: trascina file **o cartelle**, import ricorsivo, avanzamento e riepilogo.
- **Memoria / Wiki**: navigazione dei nodi come pagine + vista **grafo** (cluster tematici, archi)
  + ricerca semantica.
- **Co-scrittura / Interazione** (modalità A e D, §7.6): chat con l'LLM per **definire e completare
  i contenuti** (analisi lacune, domande, suggerimenti) e scegliere ordinamento e **stile**; a lato
  l'**outline** editabile (drag-and-drop) con **contatore parole** live (obiettivo vs stimato).
  Un pannello **Intenti/Decisioni** mostra e persiste ciò che è stato deciso e le domande aperte.
- **Authoring interattivo** (D, §8.7): flusso guidato da vuoto → bozza completa, sezione per sezione.
- **Rewrite** (B, §8.5): vista **affiancata originale ↔ riscritto**, preview del primo capitolo,
  controlli di stile; approvazione → esecuzione sull'intero libro.
- **Translate** (C, §8.6): come Rewrite ma **originale ↔ traduzione**, scelta lingua, glossario.
- **Stesura**: avanzamento per sezione, anteprima del testo generato.
- **Export**: scelta formato (DOCX/PDF/wiki), metadati libro, apertura del risultato.

**Tecnologia GUI:** da confermare (vedi domanda nel thread). Opzioni valutate: app web locale
(FastAPI + frontend nel browser) · desktop nativo (PySide6/Qt) · prototipazione rapida
(Streamlit/Gradio). Qualunque sia, **non tocca il core**: parla con gli stessi moduli della CLI.

### 9.2 CLI (strato scriptabile, sotto la GUI)

```
bookwriter project  new|list|switch|delete|info [--target-words N]
bookwriter ingest   <file|dir…> [--recursive] [--include "*.pdf,*.docx"] [--exclude …] [--watch]
                                                  # file singoli O intere directory (ricorsivo, dedup, checkpoint)
bookwriter memory   stats|search <q>|embed|graph  # ispezione/indicizzazione memoria/wiki
bookwriter style    extract <path> | new | list | show <id>   # gestione StyleProfile
bookwriter spec     set --target-words N [--tolerance 5]       # target di parole del libro (§7.5)
bookwriter write                                  # modalità A: co-scrittura (frammenti → libro)
bookwriter author                                 # modalità D: authoring interattivo (da zero)
bookwriter rewrite  <libro> [--preview] [--target-words N|--ratio R]   # modalità B: riscrittura stile
bookwriter translate <libro> --to <lingua> [--preview] [--ratio R]     # modalità C: traduzione
bookwriter export   --format docx|pdf|wiki [--outline <id>]
bookwriter gui                                    # avvia l'interfaccia grafica
```

`rewrite --preview` riscrive solo il primo capitolo e mostra **originale ↔ riscritto** per approvare
lo stile prima di elaborare l'intero libro (§8.5).

---

## 10. Considerazioni di scala (migliaia di nodi)

- Atomizzazione ed embedding **batch + concorrenti + a checkpoint** (tabella `Job`), cache per hash.
- Relation-mining **solo su top-k vicini** (no O(n²)).
- Clustering **gerarchico** per set grandi.
- Tutte le operazioni LLM idempotenti e riprendibili; log dei token per stimare i costi/tempi.

---

## 11. Roadmap a milestone

| Fase | Stato | Contenuto | Esito verificabile |
|---|---|---|---|
| **0 — Scaffolding** | ✅ fatto | struttura progetto, client LM Studio (chat+embedding+structured output, retry), multi-progetto, `registry.db` | `project new/list/switch` + `doctor` OK (verificato) |
| **1a — Ingestione+Chunking** | ✅ fatto | loader txt/md/pdf/docx/epub, **ingestione ricorsiva di directory** (dedup/checkpoint), **chunking deterministico**, BookSpec (target parole) | `ingest <cartella> -r` + `memory stats` (verificato: libro 8k parole → chunk) |
| **1b — Memoria semantica** | ✅ fatto | embedding nomic + **LanceDB** + ricerca semantica `memory embed/search` | ricerca semantica corretta su corpus reale (verificato) |
| **1c — Atomizzazione + grafo** | ✅ fatto | chunk→nodi via LLM (structured output), metadati (temi/entità/date), grafo top-k | verificato: `atomize` + `memory graph` (nodi e archi) con test verdi |
| **2 — Organizzazione tematica + resa verbatim** | ✅ fatto | **clustering per embedding** (KMeans + auto-k silhouette) o community; outline tematica; **compilazione verbatim** (§8.0) → Markdown/DOCX (Pandoc) | verificato: corpus misto → antologia per tema pulita in DOCX/MD |
| **3 — Interazione + Intenti (memoria)** | ✅ fatto | motore di co-scrittura (§7.6): analisi lacune, domande LLM, completamento→nuovi nodi (source=dialogue); **persistenza intenti/decisioni/sessioni** (§7.7) + brief di ripresa | verificato: `ask`/`answer` + `session brief` ricostruiscono lo stato |
| **4 — Stile** | ✅ fatto | StyleProfile (9 dimensioni; extract/abstract/authors+pesi+blend), StyleContract, critic loop; CLI `style` + pannello GUI | verificato: estratto stile hard-boiled → applicato con trasformazione forte |
| **5 — Stesura narrativa + Export** | ✅ fatto | compose per-capitolo (**section-aware**, capitoli→sezioni→nodi) con StyleContract + continuità, **budget parole + riconciliazione**; export **DOCX/MD/PDF** + **wiki/archivio**; CLI `compose`/`export`/`outline sections` + GUI | verificato: libro 239/250 parole; PDF valido; sezioni rese come `###` |
| **6 — Rewrite (B)** | ✅ fatto | pipeline deterministica chunk-per-chunk, `StyleContract`+`ContinuityState` (glossario/riassunto/coda) con checkpoint resumable, **preview primo capitolo**; CLI `rewrite` + pannello GUI | verificato: libro 2 capitoli riscritto in stile fiabesco uniforme, preview + export |
| **7 — Translate (C)** | ✅ fatto | riusa il motore di B con `mode=translate` + lingua target + glossario bilingue coerente, preview; CLI `translate` + pannello GUI | verificato: racconto IT→EN fedele, stile mantenuto, nomi coerenti |
| **8 — Authoring interattivo (D)** | ✅ fatto | brief persistito + `expand_topic` (LLM genera idee da zero) + `author draft` (grafo→organizza→stesura→export); CLI `author` + pannello GUI | verificato: da progetto vuoto → libro di 2 capitoli, 310/300 parole |
| **9 — Ordinamenti aggiuntivi** | ✅ fatto | **cronologico** (per date estratte), **narrativo** (arco via LLM), **custom** (criterio in linguaggio naturale); CLI `organize --ordering` + combo GUI | verificato: stessi nodi → 4 ordinamenti (tema/tempo/arco/criterio) diversi |
| **GUI (PySide6)** | 🟡 pannelli pronti | finestra desktop: barra progetto con **contatore parole** (stimate/target), pannelli **funzionanti** (Ingestione con **drag-and-drop**, Memoria+ricerca, Organizza&Export, **Stile**, **Rewrite** e **Translate** con preview, Interazione, Brief) + **stesura narrativa** nel pannello Organizza + pannello **Authoring** + pannello **Outline (editing drag-drop: riordina/rinomina/elimina capitoli, sposta/rimuovi nodi)**, worker in background, auto-refresh al cambio tab; **tutti i pannelli attivi (nessun placeholder)** | verificato offscreen; `bookwriter gui` sul desktop |
| **10 — Hardening scala** | ✅ fatto | **concorrenza** embedding/atomizzazione (config `concurrency`), **conteggio token**, **libreria stili globale**, **checkpoint `Job`** con progresso persistito (`jobs`), **clustering scalabile** (MiniBatchKMeans + silhouette campionata), **passaggio globale di coerenza** (glossario canonico), **ricerca sui nodi** | verificato: jobs persistiti, coerenza uniforma i nomi, ricerca semantica sui nodi |

---

## 12. Rischi e mitigazioni

- **Qualità atomizzazione** (nodi troppo grossi/fini) → parametri di granularità + revisione a campione in TUI.
- **Date inaffidabili** per l'ordine cronologico → `date_confidence`, fallback a ordine di ingestione, conferma in dialogo.
- **Deriva stilistica** in testi lunghi → critic loop per sezione + checklist stilistica + passaggio globale.
- **Costi/tempi LLM a scala** → batching, cache, top-k, log token; modelli scelti per fase.
- **Coerenza narrativa** su molti nodi → memoria di continuità (entità, timeline) iniettata nella stesura.

---

## 12.5 Setup LM Studio rilevato (2026-07-24) e taratura risultante

Verifica eseguita su `http://localhost:1234` — chat ed embedding funzionanti.

- **Modello reasoning/chat/stesura (default):** `gemma-4-12b-obliterated` — 131k ctx, Q8_0, `tool_use`.
- **Modello embedding (default):** `text-embedding-nomic-embed-text-v1.5` — **768 dim**, **ctx max 2048 token**.
- **Disponibili (non caricati)** per job pesanti/alta qualità: `nemotron-cascade-2-30b-a3b` e
  `nvidia/nemotron-3-nano` (ctx **1M**), `qwen3-coder-next`, vari 30–35B. Il design **non dipende**
  da contesti lunghi (continuità via stato esplicito), ma sono un'opzione.

**Taratura derivata:**
1. **Budget chunk ≈ 1500 token** (sotto il cap embedding 2048; buona granularità per atomizzazione/riscrittura). Parametrico in `config.toml`.
2. **Prefissi nomic**: indicizzare i nodi con `search_document:` e le query con `search_query:` (qualità retrieval).
3. **Structured output** via `response_format: {type:"json_schema", …}` di LM Studio per metadati/StyleCard/outline (non affidarsi al prompt: il modello tende a incorniciare in ```json).
4. **Ruoli modello configurabili per progetto** in `config.toml` (reasoning / embedding / eventuale modello "heavy" per revisione globale).

## 13. Prossimo passo operativo

Fase 0 → 1: scaffolding del progetto + client LM Studio + `project`/`ingest`/`memory` minimi,
poi spike per decidere se agganciare `graphify`/`wiki-import` o implementare i loader nativi.
Prima però conviene **verificare LM Studio** (attivo? quali modelli chat+embedding caricati?) per
tarare lo stack su ciò che hai realmente in locale.
```
