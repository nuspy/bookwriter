# bookwriter SaaS — piano d'implementazione (books.tadamoo.com)

> **Per chi lo esegue.** Questo documento è scritto per essere eseguito da Claude Code, passo per
> passo, nell'ordine dato. Ogni fase ha: obiettivo, file da creare o toccare, interfacce esatte,
> test che devono passare, criterio di «fatto». I punti marcati **⛔ DECISIONE** o **🔐 AZIONE
> DEL TITOLARE** richiedono il proprietario (credenziali, DNS, produzione): ci si ferma e si
> chiede. Nessun passo tocca la produzione di tarots, ForexGPT o gli altri servizi del cluster.

---

## 0. Sintesi

bookwriter diventa un servizio web multi-tenant su **https://books.tadamoo.com**:

- **un dominio solo**, come tarots.chat: `/` (app web), `/api/` (FastAPI), `/admin/` (console),
  `/auth/` (Keycloak);
- **tenant isolati**: libri, progetti, documenti, stili, ricerche, registri, esportazioni,
  consumi, code di lavoro — tutto separato per tenant, e dentro il tenant per progetto;
- **utenti multipli per tenant**, login **diretto** (l'utente non sceglie il tenant: lo dice il
  token), gestione degli account del proprio tenant per l'**amministratore del tenant**, che vede
  anche le **statistiche dei suoi utenti** (lavori fatti, token usati e per cosa);
- **amministrazione di piattaforma via web** per il titolare e gli interni: gestione dei tenant,
  loro amministratori, utenti interni, piani e quote, **tutti gli strumenti di impostazione**
  (profili dei modelli, ricerca, giudizi rapidi Jev, verifica della qualità), statistiche per
  tenant e per i propri utenti, salute;
- **utenti creati anche a mano**, con nome utente e password, dalla console della piattaforma e da
  quella del tenant (oltre all'invito per email);
- **riuso del cluster k3s esistente**: PostgreSQL 18 (`tadamoo/postgresql`), Redis
  (`tadamoo/redis`), Keycloak 26.0.8 (istanza `tarots/tarots-keycloak`, realm nuovo
  `books`), nginx-proxy-manager per TLS, il percorso di build e import delle immagini di tarots;
- **il motore resta quello del desktop**: lo stesso pacchetto `bookwriter` (fasi, ricerca, piano
  delle scene, revisione editoriale, completa/aggiorna, sorpresa, composizione) gira in worker
  che eseguono i lavori lunghi; l'API ne espone le operazioni; il frontend riproduce le schede
  dell'app desktop, compresa la **Qualità** (voti da 0 a 10 per capitolo e per libro, correzione
  automatica); le **Impostazioni** non sono degli utenti: stanno nella console della piattaforma.

Il desktop non si butta: resta il client locale, e condivide il motore.

---

## 1. Stato attuale verificato (24/10/2026)

**Cluster `tadamoo.com`** (k3s, nodo unico: 12 vCPU, 62 GB RAM, 1,2 TB disco, 19% usato):

| componente | dove | note |
|---|---|---|
| PostgreSQL 18 | `tadamoo/postgresql` (ClusterIP 5432) | già usato da tarots (ruolo+db `tarots`, schema `keycloak`) |
| Redis 7.4 | `tadamoo/redis` (ClusterIP 6379) | condiviso |
| Keycloak 26.0.8 | `tarots/tarots-keycloak` (ClusterIP 8080, `KC_HTTP_RELATIVE_PATH=/auth`) | immagine `tarots-keycloak:v7` con tema e SPI di tarots; **Organizations** disponibili (GA in 26.0) |
| edge TLS | nginx-proxy-manager (container `npm` + deploy `tadamoo/nginx-proxy-manager`, NodePort 30080/30443) | Let's Encrypt per host |
| storage | StorageClass `local-path` (default) | RWO, nodo unico |
| immagini | nessun registry: `docker save … \| ssh root@tadamoo.com "k3s ctr -n k8s.io images import -"` | si costruisce **in locale** (Docker Desktop), mai sul server |
| posta | stalwart (`mail.tadamoo.com`) | per Keycloak (verifica email, inviti) |
| monitoraggio | grafana/prometheus/uptime-kuma (docker) | da collegare (fase 9) |

**Il riferimento da copiare** è il deploy di tarots (`E:\Projects\T-AI-rots\deploy\k8s\LEGGIMI.md`,
`tarots.yaml`): gateway nginx a percorsi, Keycloak con `KC_HTTP_RELATIVE_PATH=/auth`, segreti
minimi più chiave master Fernet, CronJob di manutenzione, frontend Next.js con
`oidc-client-ts`, backend FastAPI con Alembic.

**Il desktop oggi** (`E:\Projects\bookwriter`): Python 3.10, PySide6. Stato per progetto su
file: `~/.bookwriter/projects/<slug>/` con `config.toml`, `project.db` (SQLite via SQLModel),
`vectors.lance` (LanceDB), `sources/`, `exports/`, `book/` (forge: `.forge/state.json`,
`chapters/`, `research/`, `story/`, `output/`), `completa/`, `aggiorna/`, `saves/`. Il motore è
già separato dalla GUI (`bookwriter/forge/engine.py:Forge`, `open_forge(paths, cfg, client,
mode)`), riceve callback `progress`/`note`/`should_stop` e scrive tutto su disco: è la base del
worker. Il client dei modelli (`bookwriter/llm/client.py`) passa da **llmswitch** (registro di
fornitori locali e cloud).

### 1.1 Novità del desktop dal 24/10/2026 (da portare nel web)

| novità | dove nel desktop | effetto sul SaaS |
|---|---|---|
| Schede raggruppate per lavoro: «Scrivi libro da appunti» (importa, prepara, struttura, componi, riepilogo), «Scrivi da zero», «Completa un libro» e «Aggiorna un libro» (a fasi + sotto-scheda), «Riscrivi», «Traduci», «Stile», «Leggi anteprima», **«Qualità»** (Un capitolo · Il libro intero), «Utilità» (coda, consumi, impostazioni) | `gui/main_window.py` (`tab_specs`, `_subtabs`) | stessa struttura nella navigazione web (§7.3) |
| Avvio automatico di una fase solo se non è già conclusa **e** la fase prima è conclusa o chiusa a mano dall'autore (`ForgeState.skipped`) | `Forge.can_autostart_research`, `phase_concluded` | l'API non avvia job da sola: il client chiama `…/autostart` che applica la stessa regola |
| Piano delle scene con variabili che cambiano, domanda nuova, ragionamento, funzione della ricerca, ponte dal capitolo prima e fili aperti; controllo di continuità del piano | `forge/scenes.py`, `forge/timeline.py:check_plan` | nessuna (motore) |
| **Registro degli eventi** per capitolo (chi è dove, a che ora, oggetti, porte, scadenze): fatti fissati durante la stesura, controlli deterministici e verifica dopo le correzioni | `forge/timeline.py`, `story/timeline/` | `GET …/timeline/{n}` per mostrarlo |
| Revisione editoriale: significato sul capitolo intero, griglia in tre controlli, provenienza delle fonti, segno «da rivedere» | `forge/editor.py` (`review_flags`) | `GET …/editorial` con l'elenco e i segni |
| **Verifica della qualità**: 18 criteri + globale + potenziale (stima), 0-10, con il metro del tipo di libro (romanzo, racconti, memoir, divulgativo, saggio, manuale, accademico) e dello scopo; correzione di **un problema alla volta, solo sulla sua parte**, poi rivalutazione; tetto dei giri nelle impostazioni, oltre il tetto ogni giro va autorizzato; ogni versione salvata e ripristinabile; libro intero con correzione dei capitoli scelti; qualsiasi file valutato su una copia | `forge/quality.py`, `Forge.evaluate_chapter_quality / improve_chapter_quality / evaluate_book_quality`, `open_quality_workspace`, `gui/quality_view.py` | pagine Qualità (§7.3), API (§6.5), job con attesa di autorizzazione (§6.4) |
| Modello dei controlli separabile da quello che scrive (`llm.models.review`) | `config.py:ModelRoles.checker` | campo del profilo modelli |
| Jev configurabile: indirizzo (predefinito `JEV_DEFAULT_URL`), modello, richieste insieme, tempo massimo; un servizio compatibile locale non chiede la chiave | `config.py:ResearchSettings`, `forge/jev.py` | impostazioni di piattaforma (§6.6, §7.4) |
| Soglie della qualità (`quality.min_global`, `min_critical`, `max_rounds`) | `config.py:QualitySettings` | impostazioni di piattaforma, con eccezione per tenant |
| Anteprima anche dei libri dei tre lavori; autore e data delle fonti dai metadati delle pagine | `preview.py`, `pagetext.page_meta` | già previsti in `…/preview` |
| Desktop in **italiano e inglese** (le altre lingue ricadono sull'inglese) | `gui/i18n.py` | il web è in inglese, tradotto dal browser (§7.5) |

**Il server non ha GPU** (da verificare: 🔐 `nvidia-smi` sul server). I modelli in SaaS sono
quindi: fornitori cloud via llmswitch, **oppure** il PC del titolare con LM Studio come *worker
remoto* (fase 7).

---

## 2. Decisioni (prese dal titolare il 25/10/2026)

| # | decisione | scelta | alternativa scartata |
|---|---|---|---|
| ✅ D1 | Keycloak | **realm `books` nell'istanza esistente `tarots-keycloak`**, con *Frontend URL* del realm = `https://books.tadamoo.com/auth` e il gateway di books che inoltra `/auth/` al servizio `tarots-keycloak.tarots.svc:8080`. Zero infrastruttura nuova. | istanza `books-keycloak` dedicata (resta la via di migrazione: export/import del realm) |
| ✅ D2 | modelli | fornitori cloud configurati dalla piattaforma (chiavi cifrate), con quota per tenant; il PC del titolare come pool `local-gpu` opzionale (fase 7) | solo PC del titolare; solo cloud |
| ✅ D3 | ingresso dei tenant | **solo su invito** dalla piattaforma (il titolare o lo staff crea il tenant e il suo amministratore) | iscrizione libera con tenant personale |
| ✅ D4 | fatturazione | fuori dalla v1: quote manuali per piano | Stripe subito |
| ✅ D5 | lingua dell'interfaccia web | **interfaccia scritta in inglese (base e ripiego), tradotta sul dispositivo dalla Translator API del browser**, desktop e mobile, con lo stesso motore di T-AI-rots (`frontend/v2/lib/traduzione-auto.ts` + `usa-traduzione.ts`, vedi §7.5) | 12 cataloghi copiati dal desktop; base italiana |

---

## 3. Architettura

```
                 Internet
                    │ HTTPS books.tadamoo.com
          nginx-proxy-manager (TLS, Let's Encrypt)
                    │ http → NodePort books-gateway
┌──────────────── namespace books ───────────────────────────────────────────┐
│ books-gateway (nginx :8080)                                               │
│   /          → books-web     (Next.js :3000)                              │
│   /admin/    → books-admin   (Next.js :3000, basePath /admin)             │
│   /api/      → books-api     (FastAPI :8000)   [strip /api]               │
│   /auth/     → tarots-keycloak.tarots.svc:8080  (realm books)             │
│                                                                           │
│ books-api ── Postgres db `books` (tadamoo/postgresql)                     │
│     │   └─ Redis db 4 (tadamoo/redis): eventi dei lavori, annullamenti     │
│     │                                                                     │
│ books-worker ×N  ── esegue il motore bookwriter sui file del tenant        │
│ books-trawl (Bun + Camoufox, ClusterIP :8191) ← pagine bloccate            │
│ PVC books-data (local-path) → /data/tenants/<tenant>/projects/<project>/   │
└───────────────────────────────────────────────────────────────────────────┘
        ▲ HTTPS (token di worker)
   PC del titolare: books-worker «local-gpu» + LM Studio (fase 7, opzionale)
```

**Principi.**

1. **Il tenant non si sceglie, si ricava.** Ogni richiesta porta un JWT del realm `books`; il
   tenant è il claim `organization` (Keycloak Organizations). L'API non accetta mai un
   `tenant_id` dal client per le operazioni di un utente normale.
2. **Il disco segue la stessa regola.** Ogni percorso si costruisce lato server da
   `(tenant_id, project_id)` con una sola funzione (`storage.project_root`), che rifiuta
   qualunque segmento non canonico: niente path traversal possibile.
3. **I lavori lunghi non stanno nell'API.** L'API crea un `job` in Postgres; un worker lo prende
   (`SELECT … FOR UPDATE SKIP LOCKED`), esegue il motore, pubblica eventi su Redis; il client li
   legge in SSE. Fermare = flag su Redis letto da `should_stop`.
4. **Il motore non sa di essere in un SaaS.** Riceve `ProjectPaths`, `AppConfig`, un client LLM
   e i tre callback, come nel desktop. Tutto quello che è multi-tenant sta in `bookwriter.server`.
5. **Quote prima del lavoro.** Un job parte solo se il tenant ha quota (token stimati, lavori
   concorrenti, spazio): rifiuto esplicito con messaggio, mai a metà.

---

## 4. Identità e permessi (Keycloak 26, realm `books`)

### 4.1 Realm

- realm `books`, *Frontend URL* `https://books.tadamoo.com/auth`, login con **nome utente o
  email** (`loginWithEmailAllowed=true`, `registrationEmailAsUsername=false`: gli utenti creati a
  mano possono non avere un'email), verifica email via stalwart (`mail.tadamoo.com:587`) per chi
  ce l'ha, password policy (12+, non uguale al nome utente né all'email), brute-force detection,
  sessioni 10 h / inattività 2 h, refresh token rotation.
- **Organizations** abilitate; un'organizzazione = un tenant. Il claim `organization` entra nel
  token del client `books-web` (mapper «Organization Membership», multivalore spento: un utente
  appartiene a un solo tenant in v1).
- tema: quello di default di Keycloak con logo e colori di bookwriter tramite *realm
  attributes* (nessuna ricostruzione dell'immagine tarots). Un tema dedicato richiede D1 →
  istanza dedicata.

### 4.2 Client

| client | tipo | uso |
|---|---|---|
| `books-web` | pubblico, Authorization Code + PKCE | app web; redirect `https://books.tadamoo.com/*` |
| `books-admin` | pubblico, PKCE | console `/admin`; entra solo con `platform_admin`/`platform_staff`/`tenant_admin` |
| `books-api` | confidenziale, service account | l'API verso l'Admin REST di Keycloak (inviti, utenti creati a mano, password, lingua, ruoli, organizzazioni); ruoli del service account: `manage-users` (**c'è già**: serve anche per cambiare lingua e password), `view-users`, `manage-organizations`, ristretti al realm `books` |

**`manage-users`, chi lo ha** (indicazione del titolare, 25/10/2026): il service account lo ha già
(è lo stesso ruolo con cui oggi si cambiano lingua e password); gli **admin dell'app** lo hanno
già per impostazione sui propri tenant e sui propri utenti; gli **admin del tenant** lo hanno già
sui propri utenti. Non si crea quindi nessun ruolo nuovo in Keycloak: l'API usa il service
account per le chiamate e, **prima** di ogni chiamata, controlla che chi la chiede abbia il
permesso su quel bersaglio (§4.3) — un `tenant_admin` solo sugli utenti del proprio tenant.

**Se il ruolo mancasse** (realm rifatto, client ricreato): Keycloak risponde 403 e l'API
risponde **502** con il nome del ruolo che manca, senza nascondere il problema dietro un errore
generico: `{"error": {"code": "keycloak_role_missing", "message": "Il service account books-api
non ha il ruolo manage-users nel realm books", "role": "manage-users"}}`. Lo stesso per
`view-users` e `manage-organizations`. `/ready` lo verifica all'avvio leggendo i ruoli del
service account, e la console della piattaforma lo mostra in «Salute».

### 4.3 Ruoli (realm roles)

| ruolo | chi | può |
|---|---|---|
| `platform_admin` | il titolare (ha già `manage-users` sui propri tenant e utenti) | tutto: tenant (crea, sospendi, piano, quote, eccezioni alle impostazioni), loro amministratori e utenti, interni, **tutti gli strumenti di impostazione** (profili modelli, ricerca, Jev, qualità, piani), statistiche per tenant e per gli interni, audit |
| `platform_staff` | interni del titolare | gestire i tenant e i loro utenti (anche creandoli a mano); i propri utenti interni; statistiche; non le impostazioni né le chiavi |
| `tenant_admin` | amministratore di un tenant (ha già `manage-users` sui propri utenti) | utenti del proprio tenant (invita **o crea a mano con nome utente e password**, disattiva, ruoli `author`/`viewer`/`tenant_admin`), stili condivisi, quote interne, **statistiche per utente** (lavori, token e per cosa), soglie di qualità del tenant (entro i limiti della piattaforma) |
| `author` | utente del tenant | progetti propri e condivisi con lui; tutte le funzioni dell'app |
| `viewer` | utente del tenant | leggere i progetti condivisi, scaricare le esportazioni |

Gli interni stanno nell'organizzazione speciale `platform` (tenant `platform`, mai usato per
dati di clienti). Un `platform_*` può agire su un tenant **solo** dalla console, con audit; non
vede i libri dei tenant se non con la funzione esplicita «accesso di assistenza» (fase 8),
temporanea, motivata e registrata.

### 4.4 Login diretto

L'utente apre `books.tadamoo.com`, preme «Entra», fa login sul realm `books` senza indicare il
tenant. Il token contiene `organization: {"<alias>": {"id": "<uuid>"}}`; l'API mappa l'id
all'interno `tenants.kc_org_id`. Un utente senza organizzazione (o con il tenant sospeso) vede una
pagina «Account non associato a un'organizzazione attiva» e non accede a niente.

---

## 5. Dati

### 5.1 Postgres — database `books` (ruolo `books`)

Migrazioni con **Alembic** in `bookwriter/server/migrations/`. Tutte le tabelle di dominio hanno
`tenant_id` NOT NULL e indice; le query passano da un *repository* che aggiunge sempre il filtro
(vedi 6.3). Oltre al filtro applicativo, **Row Level Security** su Postgres come seconda difesa:
`SET app.tenant_id = …` per transazione e policy `tenant_id = current_setting('app.tenant_id')`.

```sql
tenants(id uuid pk, slug text unique, name text, status text check (status in ('attivo','sospeso','chiuso')),
        plan text, kc_org_id uuid unique, quotas jsonb, created_at timestamptz, created_by uuid)
users(id uuid pk /* = sub di Keycloak */, tenant_id uuid fk, email text, display_name text,
      ui_language text, ui_theme text, last_seen timestamptz, status text)
projects(id uuid pk, tenant_id uuid, owner_id uuid, name text, slug text, created_at, archived_at,
         unique(tenant_id, slug))
project_members(project_id uuid, user_id uuid, role text check (role in ('owner','editor','viewer')),
                primary key(project_id, user_id))
styles(id uuid pk, tenant_id uuid, owner_id uuid null /* null = condiviso nel tenant */, name text,
       origin text, data jsonb, created_at)
documents(id uuid pk, tenant_id, project_id, filename text, size bigint, sha256 text, mime text,
          storage_key text, uploaded_by uuid, uploaded_at)
jobs(id uuid pk, tenant_id, project_id, user_id, kind text, mode text /* nuovo|completa|aggiorna */,
     params jsonb, pool text default 'cloud',
     status text /* in_coda|in_corso|attende_autorizzazione|fatto|fermato|fallito */,
     progress jsonb, result jsonb, error text, created_at, started_at, finished_at, worker_id text,
     cancel_requested bool default false)
job_events(id bigserial pk, job_id uuid, ts timestamptz, kind text /* note|progress|section|done|error */,
           payload jsonb)          -- copia durevole degli eventi (Redis è il canale live)
usage(id bigserial pk, tenant_id, user_id, project_id, job_id, job_kind text,
      activity text /* per cosa: vedi ACTIVITIES (§6.7) */, model text,
      prompt_tokens int, completion_tokens int, cost_eur numeric(12,6), ts timestamptz)
usage_daily(tenant_id uuid, user_id uuid, day date, job_kind text, activity text, model text,
      jobs int, jobs_done int, jobs_failed int, prompt_tokens bigint, completion_tokens bigint,
      cost_eur numeric(14,6), primary key(tenant_id, user_id, day, job_kind, activity, model))
                                    -- aggregato per le statistiche: aggiornato a fine job e da un
                                    -- CronJob notturno che lo ricalcola dagli eventi (idempotente)
platform_settings(section text pk /* models|research|jev|quality|writing */, data jsonb,
      secrets_enc bytea /* chiavi: scritte, mai rilette in chiaro */, updated_by uuid, updated_at)
tenant_settings(tenant_id uuid pk, data jsonb /* lingua predefinita, soglie di qualità entro i
      limiti della piattaforma, quote interne per utente */, updated_by uuid, updated_at)
llm_profiles(id uuid pk, scope text /* platform|tenant */, tenant_id uuid null, name text,
             provider text, base_url text, models jsonb, reasoning jsonb, sampling jsonb,
             api_key_enc bytea, updated_by uuid, updated_at)
plans(id text pk, name text, quotas jsonb)      -- token/mese, lavori concorrenti, GB, progetti, utenti
audit_log(id bigserial pk, tenant_id uuid null, actor uuid, actor_role text, action text,
          target text, details jsonb, ip_hash text, ts timestamptz)
system_secrets(name text pk, value_enc bytea)  -- come tarots: segreti generati e cifrati
```

### 5.2 Disco — PVC `books-data`

```
/data/tenants/<tenant_uuid>/
    projects/<project_uuid>/          ← è una ProjectPaths del desktop, identica:
        config.toml                    generato dall'API dal profilo modelli (mai scritto dall'utente)
        project.db                     SQLite del motore (nodi, outline, stili locali) — v1
        vectors.lance/                 LanceDB del progetto
        sources/  exports/  saves/
        book/  completa/  aggiorna/    le tre cartelle del motore a fasi; in ciascuna anche
                                       quality/ (valutazioni), story/versions/ (versioni dei
                                       capitoli corretti), story/timeline/ (registro degli eventi)
        valutazioni/<nome>/            copie di file qualsiasi da valutare e correggere
    styles/                            esportazioni degli stili condivisi
/data/tmp/<job_uuid>/                  file temporanei dei lavori, cancellati alla fine
```

**v1 tiene SQLite e LanceDB per progetto** perché il motore li usa così (un solo scrittore per
progetto, garantito dal lock del job: vedi 6.4). La migrazione di `project.db` a Postgres è la
fase 10, non un prerequisito.

Spazio: quota per tenant calcolata da un CronJob notturno (`du` per tenant → `tenants.quotas.used_bytes`).

---

## 6. Backend

### 6.1 Struttura del codice

Nel repository bookwriter, accanto al desktop:

```
bookwriter/
  server/
    __init__.py
    app.py              # FastAPI: create_app(settings) ; router /v1/*
    settings.py         # pydantic-settings, prefisso BOOKS_ ; validate_production()
    db.py               # SQLAlchemy 2 async (asyncpg) ; sessione con SET app.tenant_id
    models.py           # ORM delle tabelle §5.1
    migrations/         # alembic
    auth.py             # verifica JWT (JWKS del realm, cache 10 min) → Principal(user, tenant, roles)
    tenancy.py          # dipendenze FastAPI: current_principal, require_role, project_access
    storage.py          # project_root(tenant, project) -> ProjectPaths ; guardie sui percorsi
    secrets.py          # Fernet con la chiave master ; cifra/decifra api_key_enc
    llm_profiles.py     # AppConfig del progetto dal profilo (tenant > piattaforma)
    quotas.py           # check_quota(tenant, kind) ; addebito usage
    keycloak_admin.py   # client Admin REST: org, utenti, inviti, ruoli
    jobs.py             # crea/elenca/annulla job ; eventi SSE
    routers/
      me.py projects.py documents.py forge.py notes.py rewrite.py translate.py
      styles.py preview.py jobs.py usage.py tenant_admin.py platform_admin.py health.py
  worker/
    __init__.py
    main.py             # loop: lease job → esegui → eventi → fine ; heartbeat
    runners.py          # una funzione per kind di job → chiama il motore
    events.py           # Notifier: progress/note/section → Redis XADD + job_events
    llm.py              # client del motore costruito dal profilo (usa llmswitch) + conteggio usage
```

Dipendenze nuove (extra `server` in `pyproject.toml`): `fastapi`, `uvicorn[standard]`,
`sqlalchemy>=2`, `asyncpg`, `alembic`, `pydantic-settings`, `pyjwt[crypto]`, `httpx`,
`redis>=5`, `cryptography`, `python-multipart`, `sse-starlette`. Il desktop non le richiede.

### 6.2 Configurazione (`settings.py`)

`BOOKS_DATABASE_URL`, `BOOKS_REDIS_URL` (db 4), `BOOKS_DATA_DIR=/data`,
`BOOKS_KEYCLOAK_ISSUER=https://books.tadamoo.com/auth/realms/books`,
`BOOKS_KEYCLOAK_INTERNAL_URL=http://tarots-keycloak.tarots.svc:8080/auth` (JWKS e Admin REST
senza uscire dal cluster), `BOOKS_KC_API_CLIENT_ID=books-api`, `BOOKS_KC_API_CLIENT_SECRET`,
`BOOKS_MASTER_KEY` (Fernet), `BOOKS_PUBLIC_URL=https://books.tadamoo.com`,
`BOOKS_TRAWL_URL=http://books-trawl:8191/v1`, `BOOKS_ENVIRONMENT=production|dev`,
`BOOKS_AUTH_DISABLED` (solo dev: rifiutato in produzione da `validate_production`).

### 6.3 Isolamento — le tre difese

1. **Dipendenza unica** `tenancy.current_principal` su ogni router non pubblico: decodifica il
   JWT (issuer, audience `books-web`/`books-admin`, `exp`, firma JWKS), ricava `tenant_id` dal
   claim `organization`, carica/aggiorna `users`, rifiuta tenant non `attivo`.
2. **Repository** (`db.TenantSession`): ogni query di dominio parte da
   `select(Model).where(Model.tenant_id == principal.tenant_id)`; nessuna funzione accetta
   `tenant_id` dal client. Test: per ogni router, un test «tenant B non vede/scrive/cancella le
   risorse di A» (tabellare, generato dall'elenco delle rotte).
3. **RLS su Postgres**: `ALTER TABLE … ENABLE ROW LEVEL SECURITY` + policy; il ruolo `books`
   non è `BYPASSRLS`; `SET LOCAL app.tenant_id` all'inizio di ogni transazione. Le operazioni di
   piattaforma usano un ruolo separato `books_platform` (BYPASSRLS) solo nei router
   `platform_admin`.

**Disco**: `storage.project_root(tenant_id, project_id)` costruisce il percorso da UUID validati,
fa `resolve()` e verifica che stia sotto `/data/tenants/<tenant_id>/`; tutti i download passano
da `storage.safe_file(root, relative)` con la stessa verifica. I nomi dei file caricati si
riscrivono (`secure_filename` + UUID); i nomi originali vivono solo in `documents.filename`.

### 6.4 Lavori lunghi

`jobs.kind` (uno per operazione del motore che può durare più di qualche secondo):

| kind | chiama nel motore |
|---|---|
| `ingest` | `ingest.pipeline` (Importa testi) |
| `prepare.split` / `prepare.index` / `prepare.connect` | atomize, embed, graph |
| `organize` / `compose` | `organize.*`, `compose.narrative` |
| `forge.talk` | `Forge.talk` (streaming: eventi `delta`) |
| `forge.finish_interview` `forge.make_spec` `forge.research_landscape` `forge.research_chapters` `forge.review_generate` `forge.write_next` `forge.write_all` `forge.redo_short` `forge.trial` `forge.ledger` `forge.claims` `forge.import_manuscript` `forge.surprise` `forge.build` `forge.update_outputs` | metodi omonimi di `Forge` (mode nel job) |
| `rewrite` / `translate` | `rewrite.engine` (coda come nel desktop) |
| `style.extract` / `style.apply` | `style.engine` |
| `update.merge_notes` | `update.revise.revise_draft` |

Regole:

- **un job attivo per progetto** (lock: `pg_advisory_xact_lock(hash(project_id))` al lease):
  SQLite/LanceDB del progetto hanno un solo scrittore. Le letture dell'API (stato, file) restano
  libere: il motore scrive i file in modo atomico (già oggi: `os.replace`).
- **lease**: `UPDATE jobs SET status='in_corso', worker_id=…, started_at=now() WHERE id = (SELECT id
  FROM jobs WHERE status='in_coda' AND pool = :pool ORDER BY created_at FOR UPDATE SKIP LOCKED
  LIMIT 1) RETURNING *`; heartbeat ogni 15 s in Redis; un job `in_corso` senza heartbeat da 2 min
  torna `in_coda` (il motore riprende da sé: tutto è a checkpoint su disco).
- **eventi**: `Notifier` implementa `progress(i, n)`, `note(x)` (stringa o tupla `("section",
  c, k, n)` come oggi) e `should_stop()` (legge `cancel:<job>` su Redis). Pubblica su Redis Stream
  `job:<id>` e salva in `job_events` (le note, non i delta).
- **SSE**: `GET /v1/jobs/{id}/events` (autenticato, verifica tenant), riprende da
  `Last-Event-ID`.
- **usage**: il client LLM del worker conta i token per chiamata (già `_track` nel client) e
  scrive `usage` con `tenant_id/user_id/project_id/job_id`.

**Attesa di autorizzazione** (qualità). Un job `quality.improve_*` che raggiunge il tetto dei giri
mentre i giri migliorano ancora non continua da solo: passa a `attende_autorizzazione`, emette
l'evento `authorize` {capitolo, voto, soglia, giri} e libera il worker. `POST
/jobs/{id}/authorize` {continue: true|false} lo rimette in coda per **un** giro (o lo chiude). Per il
libro intero l'attesa vale capitolo per capitolo: gli altri capitoli scelti vanno avanti.

### 6.5 API (prefisso `/v1`, dietro `/api`)

Tutte JSON, errori `{"error": {"code", "message"}}` in inglese (la pagina li traduce come il resto, §7.5).
Un rifiuto di Keycloak per un ruolo mancante del service account diventa **502**
`keycloak_role_missing` con il campo `role` (§4.2); un rifiuto per un permesso che manca a chi
chiede (un `tenant_admin` su un utente di un altro tenant) è **403** dell'API, prima di chiamare
Keycloak.

**Sessione**
- `GET /me` → utente, tenant, ruoli, quote residue, preferenze; `PATCH /me` (lingua, tema).

**Progetti**
- `GET /projects` · `POST /projects` {name} · `GET/PATCH/DELETE /projects/{id}` (DELETE =
  archivia; cancellazione definitiva solo `tenant_admin`, con conferma e 30 giorni di
  ripristino) · `GET/PUT /projects/{id}/members`.

**Documenti**
- `POST /projects/{id}/documents` (multipart, max 200 MB, estensioni del loader) ·
  `GET /projects/{id}/documents` · `DELETE …/{doc}` · `GET …/{doc}/download`.

**Scrivi da appunti** (sotto-schede del desktop)
- `POST /projects/{id}/notes/ingest` {document_ids} → job · `GET /projects/{id}/notes/nodes?q=` ·
  `POST /projects/{id}/notes/prepare/{split|index|connect}` → job · `GET/PUT
  /projects/{id}/notes/outline` · `POST /projects/{id}/notes/organize` · `POST
  /projects/{id}/notes/compose` → job · `GET /projects/{id}/notes/brief`.

**Forge** (`{mode}` ∈ `nuovo|completa|aggiorna`)
- `GET /projects/{id}/forge/{mode}` → stato (fasi con `PhaseInfo`, comandi con lo stato di
  `phase_guide.command_states`, riga «cosa tocca a te» di `phase_guide.next_step`, quaderno)
- `POST …/talk` {text} → job `forge.talk` con eventi `delta` (il client mostra la risposta
  mentre arriva) · `POST …/finish-interview` · `POST …/advance` {force} · `POST …/go` {phase}
- `GET/PUT …/spec` (MASTER_SPEC.md: lettura e salvataggio a mano, come `save_spec`) · `POST
  …/spec/make` · `POST …/spec/approve`
- ricerca: `POST …/research/landscape|chapters|resume|decline` · `GET …/research` (dati per
  l'esploratore: punti, pagine, questioni) · `POST …/questions/{qid}/decide` · `POST
  …/questions/talk` (chat sui contrasti) · `POST …/research/discard`
- revisione: `POST …/review/generate|skip` · `GET …/review` · `POST
  …/review/{pid}/talk|propose|confirm|skip`
- stesura: `POST …/write/next|all|redo-short|trial/{n}` · `POST …/ledger/rebuild` · `GET
  …/editorial` (capitoli con esito e segno «da rivedere», come `editor.review_flags`) · `GET
  …/editorial/{n}` (resoconto md) · `GET …/plans/{n}` · `GET …/timeline/{n}` (registro degli
  eventi) · `POST …/autostart` (avvia la fase solo con la regola del desktop: non conclusa, e la
  fase prima conclusa o chiusa a mano)
- manoscritto: `POST …/manuscript` {document_id} (solo completa/aggiorna) · `POST
  …/update-outputs`
- sorpresa: `POST …/surprise` {description} (solo `nuovo`)
- produzione e composizione: `POST …/cover` (upload immagine) · `GET/PUT …/book-yaml` ·
  `POST …/build` {formats} · `GET …/outputs` · `GET …/outputs/{file}` · `POST …/handover`
- salvataggi: `POST …/saves` · `GET …/saves` · `POST …/saves/{name}/restore` · `POST
  …/reset`

**Qualità** (`{source}` ∈ `nuovo|completa|aggiorna|ws:<nome>`, dove `ws:` è una copia di un file
qualsiasi)
- `POST /projects/{id}/quality/workspaces` {document_id} → `ws:<nome>` (copia divisa in
  capitoli; il documento non si tocca)
- `GET/PUT /projects/{id}/quality/{source}/kind` (tipo di libro: dal quaderno o scelto)
- `GET …/quality/{source}/chapters/{n}` (storia delle valutazioni) · `POST
  …/chapters/{n}/evaluate` → job · `POST …/chapters/{n}/improve` {rounds?} → job (può fermarsi in
  `attende_autorizzazione`, §6.4)
- `GET …/chapters/{n}/versions` · `POST …/chapters/{n}/versions/{v}/restore`
- `GET …/quality/{source}/book` · `POST …/book/evaluate` → job · `POST …/book/improve`
  {chapters: [n…]} → job
- `POST …/chapters/{n}/revise` {text?, output?} → job `quality.revise_chapter`: un giro di revisione completo (`forge/revision.py`: baseline con le fonti dell'app consultate, rilievi con ID e stato, piano con le operazioni, interventi, verifica indipendente, gate A–I, verdetto) · `GET …/chapters/{n}/revisions` · `GET …/revisions/{run}` (report, diff, `run.json`)
- La risposta di una valutazione è l'`Evaluation` del motore: per criterio chiave, etichetta,
  voto 0-10 (o null se non si applica), peso, critico, verdetto, citazione, problemi (con
  sezione, gravità e correzione), misura; il globale; per il libro titoli di confronto, pubblico,
  rischi, priorità. Le etichette arrivano in italiano dal motore: il client le mappa sulle sue
  stringhe inglesi per chiave (`quality.CRITERIA`), poi le traduce il browser.

**Riscrivi / Traduci / Stile**
- `POST /projects/{id}/rewrite` {document_id, style_id, target} → job · `POST
  /projects/{id}/translate` {document_id, to} → job
- `GET/POST/DELETE /styles` (scope `private|tenant`; `tenant` solo `tenant_admin`) · `POST
  /styles/extract|abstract|authors` → job · `POST /styles/{id}/apply` → job

**Anteprima** — `GET /projects/{id}/preview/sources` · `GET /projects/{id}/preview/{source}`
(markdown; include i capitoli del forge e le prove, novità rispetto al desktop di oggi).

**Lavori e consumi** — `GET /jobs?project=` · `GET /jobs/{id}` · `POST /jobs/{id}/cancel` ·
`POST /jobs/{id}/move` {position} (coda) · `GET /jobs/{id}/events` (SSE) · `POST
/jobs/{id}/authorize` {continue} · `GET /usage?from=&to=&group=project|model|activity` (i propri).

**Statistiche** (da `usage_daily`; periodo `from`/`to`, raggruppamento a scelta, esportazione CSV)
- `GET /tenant/stats?group=user|activity|job_kind|model|project|day` (`tenant_admin`): per ogni
  utente del tenant i lavori fatti (per tipo ed esito) e i token usati (entrata/uscita, costo) e
  **per cosa** (attività: conversazione, ricerca, stesura, revisione editoriale, qualità,
  traduzione, riscrittura, stile, importazione…); `GET /tenant/stats/users/{id}` il dettaglio.
- `GET /platform/stats?group=tenant|activity|job_kind|model|day` (`platform_*`): per tenant;
  `GET /platform/tenants/{id}/stats` (come quella del tenant, vista dalla piattaforma); `GET
  /platform/staff/stats` per gli utenti interni (organizzazione `platform`).

**Amministrazione del tenant** (`tenant_admin`) — `GET/POST /tenant/users`, in due modi:
`{"mode": "invite", email, role}` (Keycloak crea l'utente nell'organizzazione con azione «verifica
email + imposta password») oppure `{"mode": "manual", username, password, temporary: true,
email?, display_name?, role}` (utente creato a mano: la password va **solo** a Keycloak, mai nel
database né nei log né nell'audit; `temporary` predefinito vero = va cambiata al primo accesso;
la policy del realm si verifica prima e l'errore si mostra) · `POST /tenant/users/{id}/password`
{password, temporary} (reimpostazione a mano) · `PATCH /tenant/users/{id}` (ruolo, sospensione) ·
`DELETE` (disattiva) · `GET /tenant/usage` · `GET/PUT /tenant/settings` (lingua predefinita, stili
condivisi, quote interne per utente, soglie di qualità entro i limiti della piattaforma).

**Amministrazione di piattaforma** (`platform_*`) — `GET/POST /platform/tenants` (crea
organizzazione KC + tenant + primo `tenant_admin`, **invitato o creato a mano** con nome utente e
password) · `PATCH /platform/tenants/{id}` (piano, quote, stato) · `GET/PUT
/platform/tenants/{id}/settings` (eccezioni alle impostazioni per quel tenant) · `GET/POST
/platform/tenants/{id}/users` (stessi due modi del tenant) · `POST
/platform/tenants/{id}/users/{uid}/password` · `GET/POST /platform/staff` (solo `platform_admin`;
stessi due modi) · `GET/PUT /platform/settings/{section}` (solo `platform_admin`; `section` ∈
`models` profili e ruoli dei modelli compreso quello dei controlli, livelli di ragionamento
generale/rapido/stesura, campionamento, modo JSON · `research` motore, chiavi, TRAWL, domini
esclusi, pertinenza · `jev` indirizzo, modello, chiave, richieste insieme, tempo massimo, soglia,
estratto massimo · `quality` soglie e tetto dei giri · `writing` tetto di parole per chiamata ·
`plans` piani e quote; le chiavi si scrivono e non si rileggono mai in chiaro; ogni modifica va
nell'audit con il prima e il dopo, chiavi escluse) · `GET/PUT /platform/llm-profiles` · `GET
/platform/usage` · `GET /platform/jobs` · `GET /platform/health` · `POST
/platform/tenants/{id}/support-access` {motivo, durata} (fase 8).

**Salute** — `GET /health` (vivo) · `GET /ready` (db, redis, keycloak JWKS).

### 6.6 Configurazione dei modelli per progetto

`settings.config_for(tenant, project)` costruisce l'`AppConfig` del motore dalle impostazioni di
piattaforma (`platform_settings`), con sopra le eccezioni del tenant decise dalla piattaforma e, per
la qualità, le soglie del tenant entro i limiti: profilo modelli (compreso `models.review`, il
modello dei controlli), `research` con TRAWL interno, Jev con indirizzo, modello e chiave della
piattaforma (D2; un servizio compatibile locale al cluster non ha chiave), `quality` (soglie, tetto
dei giri), `writing.section_words` (il tetto per chiamata del modello scelto). Il file
`config.toml` del progetto si **rigenera** prima di ogni job: gli utenti non lo toccano mai — la
scheda Impostazioni del desktop, in SaaS, è la console della piattaforma (§7.4).

### 6.7 Le attività dei consumi («per cosa»)

`ACTIVITIES` in `server/usage.py` porta le etichette con cui il desktop registra i consumi
(`_begin_usage(busy)`) a poche categorie stabili, che sono le colonne delle statistiche:
`conversazione` (intervista, chat sui contrasti, revisione), `specifica`, `ricerca` (panorama,
fonti, giudizi; Jev a parte come `giudizi_rapidi`), `stesura` (piano delle scene, scene, registro
degli eventi), `revisione_editoriale`, `qualita` (valutazione e correzione), `appunti`
(importazione, preparazione, composizione), `riscrittura`, `traduzione`, `stile`,
`composizione`. Ogni riga di `usage` ha `job_kind` e `activity`; un test controlla che ogni
etichetta usata dal motore abbia la sua categoria.

---

## 7. Frontend

### 7.1 Stack (uguale a tarots, per riuso di competenze e Dockerfile)

Next.js 16 (App Router, output `standalone`), React 19, TypeScript strict, `oidc-client-ts` +
`react-oidc-context`, TanStack Query (cache e invalidazioni), Tailwind CSS 4 con design token
(stessi colori del tema desktop: accento `#1F5FBF` giorno / `#6EA8FE` notte, «fatto» `#0F6E66` /
`#5CC8B8`), icone **lucide-react** (lineari, monocromatiche: stessa scelta del desktop),
`react-markdown` + `remark-gfm` per spec, resoconti, anteprime. Test: Vitest + Testing Library;
end-to-end Playwright (mobile 390×844 e 360×640, desktop 1280).

App: `frontend/web` (`/`) e `frontend/admin` (`/admin`, `basePath`). Pacchetto condiviso
`frontend/shared` (client API tipizzato generato da OpenAPI con `openapi-typescript`, auth,
traduzione sul dispositivo, componenti).

### 7.2 Regole di interfaccia (vincoli, non rifiniture)

Mobile-first (360/390/1280), nessuno scroll orizzontale, bersagli ≥ 44×44, niente azioni solo al
passaggio del mouse, safe area (`env(safe-area-inset-*)`), testo base 16 px e interlinea ≥ 1,5,
contrasto ≥ 4,5:1, focus visibile, `prefers-reduced-motion`, tema giorno/notte/automatico
(`prefers-color-scheme` + scelta salvata in `/me`), feedback entro 100 ms (stato ottimistico o
scheletro), CLS < 0,1. Su mobile la navigazione di primo livello è una **bottom nav** di 5 voci
(Progetti, Scrivi, Qualità, Lavori, Altro — in «Altro» stili, anteprima, consumi, account) e le
sotto-funzioni stanno in un foglio a scomparsa; su desktop è la barra di schede del desktop, con
le stesse sotto-schede (§1.1).

### 7.3 Pagine (web)

| percorso | contenuto | corrisponde nel desktop |
|---|---|---|
| `/` | progetti del tenant (miei, condivisi), nuovo progetto, ultimi lavori | barra progetti |
| `/p/[id]/appunti/{importa,prepara,struttura,componi,riepilogo}` | sotto-schede | «Scrivi libro da appunti» |
| `/p/[id]/zero` | fasi (stepper), conversazione con quaderno, pannelli di fase, riquadro di stato, **Sorpresa** | «Scrivi da zero» |
| `/p/[id]/completa` · `/p/[id]/completa/lacune` | fasi con «Carica il manoscritto» · domande sulle lacune | «Completa un libro» |
| `/p/[id]/aggiorna` · `/p/[id]/aggiorna/appunti` | fasi + «Documento delle modifiche» · integra dagli appunti | «Aggiorna un libro» |
| `/p/[id]/riscrivi` · `/p/[id]/traduci` | documento, stile/lingua, anteprima, metti in coda | «Riscrivi», «Traduci» |
| `/stili` | stili privati e del tenant: estrai, astrai, autori, applica | «Stile di scrittura» |
| `/p/[id]/anteprima` | fonti dell'anteprima, segui il lavoro in corso | «Leggi anteprima» |
| `/p/[id]/qualita/capitolo` | libro (da zero, completato, aggiornato, o un file caricato), tipo di libro, capitolo; **schede dei voti 0-10** (globale in grande, poi un criterio per scheda, con giudizio a parole e barra; critici segnati; non applicabili spenti), dettaglio con prova e problemi; «Valuta», «Migliora fino alla qualità» (con la richiesta di autorizzazione oltre il tetto), versioni e ripristino | «Qualità → Un capitolo» |
| `/p/[id]/qualita/libro` | analisi del libro intero (schede del libro: medie, arco, coerenza fra capitoli, ripetizioni, potenziale con titoli di confronto, pubblico, rischi, priorità), tabella dei capitoli con voto ed esito, scelta e correzione automatica dei capitoli | «Qualità → Il libro intero» |
| `/lavori` | coda dei lavori (miei/del progetto), avanzamento live, ferma, riordina | «Coda di lavorazione» |
| `/consumi` | consumi miei (lavori, token, per cosa); del tenant per `tenant_admin` | «Consumi dei modelli» |
| `/account` | profilo, lingua, tema, password (link a Keycloak account console) | — |

Componenti chiave da costruire una volta: `PhaseStepper`, `ChatPanel` (streaming SSE dei delta,
riprova), `NotebookPanel`, `StatusBox` (fa/tocca a te/azioni), `CommandButton` (stati
consigliato/disponibile/superato con il perché), `ResearchExplorer` (schede: opere simili,
fonti dei capitoli, questioni, pagine bloccate), `ReviewPage`, `JobProgress` (SSE),
`FileDrop` (upload con progresso), `MarkdownView`, `DiffView` (modifiche segnate),
`ScoreCards` (le schede dei voti: griglia che va da 1 colonna a 360 px a 5 a 1280, ogni scheda
un pulsante accessibile con nome «criterio: voto su 10, giudizio», colori del tema con il giudizio
sempre anche in parole), `StatsDashboard` (grafici con la skill `dataviz`: barre per utente/attività,
serie per giorno, tabella esportabile; su mobile progressive disclosure invece di tabelle larghe).

### 7.4 Pagine (admin)

`/admin` — **piattaforma** (solo `platform_*`):

| percorso | contenuto |
|---|---|
| `/admin/tenant` | lista, crea (con primo amministratore invitato o creato a mano), piano, quote, stato, eccezioni alle impostazioni, utenti del tenant, accesso di assistenza |
| `/admin/tenant/[id]/utenti` | utenti del tenant: invita, **crea a mano** (nome utente, password, password temporanea sì/no, ruolo), reimposta la password, ruolo, sospendi |
| `/admin/interni` | utenti interni (solo `platform_admin`): stessi due modi di creazione |
| `/admin/impostazioni/{modelli,ricerca,jev,qualita,scrittura,piani}` | **gli strumenti di impostazione** del desktop (scheda Impostazioni) in forma web, solo `platform_admin`: fornitori e modelli (compreso quello dei controlli), livelli di ragionamento, campionamento, modo JSON; motore di ricerca, chiavi, TRAWL, domini esclusi; Jev (indirizzo, modello, chiave, richieste, tempi, soglie); soglie e tetto dei giri della qualità; tetto di parole per chiamata; piani e quote. Ogni pagina ha «prova» (es. legge i modelli disponibili, prova Jev con un estratto finto) e lo storico delle modifiche |
| `/admin/statistiche` | per tenant: lavori (per tipo ed esito), token e costi per attività e modello, andamento per giorno; dettaglio di un tenant; i propri utenti interni |
| `/admin/lavori` · `/admin/salute` · `/admin/audit` | lavori in corso, salute dei servizi, audit |

**Tenant** (`tenant_admin`): utenti (invita, **crea a mano** con nome utente e password,
reimposta la password, ruolo, sospendi), stili condivisi, quote per utente, soglie di qualità del
tenant (entro i limiti della piattaforma), **statistiche per utente** (lavori fatti per tipo ed
esito, token e costi per attività e per modello, andamento, esportazione CSV), audit del tenant.

### 7.5 Lingue: inglese di base, traduzione sul dispositivo (D5)

L'interfaccia web si scrive **una volta, in inglese**: niente cataloghi né `t()` nel frontend.
L'inglese è la base e il ripiego: chi ha un dispositivo senza traduzione automatica legge
l'inglese. Tutte le altre lingue, **italiano compreso**, le fa il browser con la **Translator
API**, su computer e su telefono: modelli piccoli scaricati alla prima richiesta e poi usati
senza rete; il testo non esce dal dispositivo, nessun costo, nessun server.

- **Codice riusato, non riscritto**: `frontend/shared/lib/traduzione-auto.ts` e
  `frontend/shared/lib/usa-traduzione.ts` sono copie di
  `E:\Projects\T-AI-rots\frontend\v2\lib\` (stesse funzioni: traduzione per blocchi con le
  etichette `<1>…</1>` per i link, `MutationObserver` per i testi che React cambia, memoria delle
  traduzioni in `localStorage`, `ripristina()`). Uno script `npm run controlla-traduzione`
  (come in T-AI-rots) verifica che le copie restino uguali all'originale; se T-AI-rots cambia, si
  ricopia. La lingua di partenza passata al motore è sempre `en`.
- **Parole coerenti col desktop**: le etichette citate in italiano in questo piano («Riscrivi»,
  «Traduci», le fasi…) si scrivono in inglese prendendole da `bookwriter/gui/locales/en.json`, così
  web e desktop chiamano le cose allo stesso modo. Un controllo (`npm run controlla-glossario`)
  segnala le etichette delle fasi e dei comandi che divergono da quel file.
- **Selettore lingua** in `/account` e nella barra (come `admin/app/traduzione-console.tsx` di
  T-AI-rots): la scelta sta nel browser e in `PATCH /me` (preferenza). Primo accesso: la lingua
  del browser. Il riquadro di stato (`useTraduzione`) mostra «Traduci» quando il browser deve
  scaricare il modello (vuole un tocco o un clic), l'avanzamento, «Mostra l'originale» e «Non
  tradurre automaticamente». Bersagli da 44 px, raggiungibili da tastiera, non solo al passaggio
  del mouse.
- **Cosa non si traduce mai** (`translate="no"` / `data-no-trad`): il contenuto dei libri
  (capitoli, anteprime, quaderno, specifica, resoconti, ricerca), i titoli dei progetti, i nomi
  di persone e tenant, codice e percorsi. Si traduce solo l'interfaccia: tradurre un capitolo
  per sbaglio ne cambierebbe la lettura (e la scheda «Traduci» esiste per quello, con il
  modello vero).
- **Dove la traduzione non c'è** l'interfaccia resta in inglese e il selettore lo dice
  («automatic translation is not available on this device»). Nessun catalogo di ripiego: la
  base inglese è il ripiego.
- **Testi del server**: i messaggi d'errore dell'API sono in inglese e passano per la stessa
  traduzione quando compaiono nella pagina; le email di invito (Keycloak) usano i temi di
  messaggi di Keycloak nella lingua dell'utente.
- Test: Vitest su `traduzione-auto.ts` con una Translator finta (blocchi con link, ripristino,
  aree `data-no-trad` intatte); Playwright, a 390×844, 360×640 e 1280, verifica che senza API la
  pagina resti in inglese e lo dichiari, e che con una API finta la pagina si traduca. **Prova
  reale** prima del rilascio: la traduzione su un telefono Android e su un iPhone (il commento in
  `traduzione-auto.ts` di T-AI-rots dice ancora «solo desktop»: va aggiornato o smentito con la
  prova).

---

## 8. Infrastruttura (namespace `books`)

File: `deploy/k8s/books.yaml` (unico, come `tarots.yaml`), `deploy/Dockerfile.server`
(API e worker: stessa immagine, comando diverso), `deploy/Dockerfile.next` (copiato da tarots),
`deploy/Dockerfile.trawl`, `deploy/k8s/LEGGIMI.md` (procedura passo passo come tarots).

| oggetto | dettagli |
|---|---|
| `Namespace books` | |
| `Secret books-secrets` | `db-password`, `database-url`, `redis-url`, `kc-api-client-secret`, `master-key` (Fernet, generata una volta) |
| `ConfigMap books-gateway` | nginx: `/` web, `/admin` admin, `/api/` → api (strip), `/auth/` → `tarots-keycloak.tarots.svc:8080` (header `X-Forwarded-*`), `client_max_body_size 200m`, SSE senza buffer (`proxy_buffering off` su `/api/v1/jobs/`), CSP e header di sicurezza, blocco di `/auth/admin` e `/auth/realms/master` (403) |
| `Deployment books-gateway` | nginx:alpine; `Service` NodePort **30895** (verificare libero) |
| `Deployment books-web`, `books-admin` | Next.js standalone :3000, 256 Mi |
| `Deployment books-api` | uvicorn 2 worker, :8000, 512 Mi; `initContainer` `alembic upgrade head`; probe `/health` `/ready`; monta `books-data` |
| `Deployment books-worker` | stessa immagine, `python -m bookwriter.worker --pool cloud`, repliche 2 (concorrenza = repliche), 2 Gi (LanceDB, composizione PDF); monta `books-data` |
| `Deployment books-trawl` | Bun + Camoufox, :8191 ClusterIP, 1 Gi, `BROWSER_POOL_SIZE=2` |
| `PersistentVolumeClaim books-data` | local-path, 200 Gi |
| `CronJob books-quota` | notte: spazio per tenant |
| `CronJob books-backup` | notte: `pg_dump books` + tar incrementale di `/data` verso il NAS (come `tarots-backup`, chiave SSH in secret) |
| `CronJob books-cleanup` | `/data/tmp` vecchi, progetti archiviati oltre 30 giorni |

**Postgres** (🔐 il titolare esegue o approva):
```sql
CREATE ROLE books LOGIN PASSWORD '<pw>';
CREATE ROLE books_platform LOGIN PASSWORD '<pw2>' BYPASSRLS;
CREATE DATABASE books OWNER books;
REVOKE ALL ON DATABASE books FROM PUBLIC;
GRANT CONNECT ON DATABASE books TO books_platform;
```
(se D1 = istanza dedicata: `\c books` `CREATE SCHEMA keycloak AUTHORIZATION books;`)

**Keycloak** (D1 raccomandata): realm `books` importato da `deploy/keycloak/realm-books.json`
via **Admin REST** (non con `--import-realm` dell'immagine tarots, per non toccarne il deploy):
script `deploy/keycloak/crea-realm.py` che usa l'admin di bootstrap (🔐 password fornita dal
titolare al momento, mai salvata).

**DNS e TLS** (🔐): record A `books.tadamoo.com` → IP del server (o CNAME a `tadamoo.com`);
in nginx-proxy-manager un proxy host `books.tadamoo.com` → `http://<ip-nodo>:30895`, Let's
Encrypt, «Websockets support» acceso (per SSE è indifferente ma non nuoce), HSTS.

**Build e rilascio** (come tarots, mai sul server):
```powershell
docker build --provenance=false -f deploy/Dockerfile.server -t books-api:v1 .
docker build --provenance=false -f deploy/Dockerfile.next --build-arg APP=web -t books-web:v1 frontend
docker build --provenance=false -f deploy/Dockerfile.next --build-arg APP=admin --build-arg NEXT_PUBLIC_BASE_PATH=/admin -t books-admin:v1 frontend
docker build --provenance=false -f deploy/Dockerfile.trawl -t books-trawl:v1 tools/trawl
docker save books-api:v1 | ssh root@tadamoo.com "k3s ctr -n k8s.io images import -"   # e le altre
kubectl -n books set image deploy/books-api api=books-api:v1   # ecc.
```
Script `deploy/rilascia.ps1 -Versione v1 [-Solo api,web]` che fa build, import, `set image`,
attesa del rollout e verifica di `/ready`.

---

## 9. Modifiche al motore (piccole, compatibili col desktop)

| file | modifica | perché |
|---|---|---|
| `bookwriter/projects.py` | `ProjectPaths` già pura; aggiungere `ProjectPaths.for_root(path)` | il server costruisce i percorsi per tenant |
| `bookwriter/config.py` | `AppConfig.from_mapping(dict)` (senza file) | il worker passa il profilo senza scrivere toml |
| `bookwriter/llm/client.py` | hook `on_usage(model, prompt, completion)` nel costruttore | il worker addebita i consumi |
| `bookwriter/llm/client.py` | `resolve_target` non interroga LM Studio quando `provider` è cloud | i worker cloud non hanno server locale |
| `bookwriter/forge/browser_service.py` | non avviare TRAWL se `BOOKS_TRAWL_URL` è impostato | in cluster TRAWL è un servizio |
| `bookwriter/forge/engine.py` | nessuna | il motore è già a callback e su disco; qualità (`evaluate_*_quality`, `improve_chapter_quality`, `open_quality_workspace`), registro degli eventi e regola di avvio automatico (`can_autostart_research`) sono già fatti nel desktop |
| `bookwriter/forge/quality.py` | `improve_chapter` restituisce `needs_authorization`: il runner lo trasforma nello stato `attende_autorizzazione` | §6.4 |
| `bookwriter/gui/*` | nessuna | |

Test del desktop invariati e verdi a ogni passo.

---

## 10. Fasi d'implementazione

Ogni fase si chiude con: test verdi (`pytest` del motore + test della fase), lint, aggiornamento
di questo documento (stato), commit su branch `saas/<fase>`.

### Fase 1 — Scheletro del server (locale, senza Keycloak)
- `bookwriter/server/{app,settings,db,models}.py`, alembic iniziale con tutte le tabelle §5.1 e
  le policy RLS; `docker-compose.dev.yml` con postgres 18 + redis 7 + keycloak 26.0 (dev).
- `BOOKS_AUTH_DISABLED=true` solo in dev: `Principal` fisso da header `X-Dev-User`/`X-Dev-Tenant`.
- Test: migrazione su db vuoto; RLS impedisce a `books` di leggere righe di un altro tenant;
  `/health` `/ready`.
- **Fatto quando**: `uvicorn bookwriter.server.app:create_app --factory` risponde, `alembic
  upgrade head` idempotente.

### Fase 2 — Autenticazione e tenancy
- `auth.py` (JWKS con cache, validazione completa), `tenancy.py`, realm `books` di sviluppo in
  `deploy/keycloak/realm-books.dev.json` (organizations, client, ruoli, mapper), importato nel
  Keycloak di dev.
- Test: token valido → principal; token di altro realm / scaduto / senza organizzazione → 401/403;
  tabellare «tenant B non accede alle risorse di A» per ogni rotta (generato dall'app FastAPI).
- **Fatto quando**: login reale nel Keycloak di dev, `GET /v1/me` corretto.

### Fase 3 — Progetti, documenti, storage
- `storage.py` con guardie; router `projects`, `documents`; upload in streaming su disco; quota
  spazio.
- Test: traversal (`../`, percorsi assoluti, symlink) rifiutati; upload > limite rifiutato; un
  progetto nuovo ha la struttura di `ProjectPaths` e il motore desktop lo apre.

### Fase 4 — Lavori e worker
- `jobs.py`, `worker/{main,runners,events,llm}.py`, SSE, annullamento, lock per progetto, lease
  con heartbeat e recupero, addebito `usage`.
- Runner iniziali: `forge.talk`, `forge.make_spec`, `forge.write_next`, `ingest`,
  `quality.evaluate_chapter`, `quality.improve_chapter` (con l'attesa di autorizzazione).
- Test: due worker non prendono lo stesso job; annullamento entro un passaggio; worker ucciso →
  job ripreso; eventi in ordine; usage scritto; secondo job sullo stesso progetto attende.
  Modello finto (lo stesso `FintoModello` dei test del desktop).

### Fase 5 — API complete
- Tutti i router di §6.5 e tutti i runner di §6.4; `platform_settings`, `tenant_settings`,
  `llm_profiles` e `quotas`; `usage_daily` e i router delle statistiche; `keycloak_admin.py`
  (inviti, **utenti creati a mano con password**, reimpostazione, ruoli, organizzazioni) contro il
  Keycloak di dev.
- Test: un test d'integrazione per gruppo (appunti, forge nuovo, completa, aggiorna, sorpresa,
  riscrivi, traduci, stili, anteprima, **qualità** con autorizzazione oltre il tetto e ripristino
  di una versione, amministrazione tenant, piattaforma), con modello finto e ricerca spenta;
  utente creato a mano: entra con nome utente e password temporanea, deve cambiarla, la password
  non compare in database, log, audit né risposte; un `tenant_admin` vede le statistiche solo
  dei suoi utenti e non può creare utenti in un altro tenant (403 dell'API, Keycloak non viene
  chiamato); un Keycloak di dev con il service account privato di `manage-users` fa rispondere
  502 `keycloak_role_missing` con `role: manage-users`, e `/ready` lo segnala; un `platform_staff` non legge né
  scrive `/platform/settings`; le statistiche tornano con i consumi registrati dai job; OpenAPI
  esportato in `frontend/shared/openapi.json`.

### Fase 6 — Frontend web e admin
- `frontend/` (shared, web, admin) come §7; client API generato; auth PKCE; tutte le pagine di
  §7.3–7.4.
- Test: unità dei componenti chiave (`ScoreCards`: ridisposizione 1→5 colonne, tastiera,
  nomi accessibili, giudizio in parole); Playwright: login (anche con nome utente), nuovo progetto,
  conversazione (streaming), specifica, sorpresa con modello finto, qualità di un capitolo con
  richiesta di autorizzazione, coda lavori, amministrazione tenant (invito e creazione a mano),
  statistiche del tenant, impostazioni di piattaforma, su 360, 390, 1280; axe-core senza
  violazioni serie; Lighthouse ≥ 90 accessibilità.

### Fase 7 — Pool `local-gpu` (opzionale, D2)
- Endpoint `/v1/internal/worker/lease|heartbeat|events|complete` autenticati con token di
  worker (Secret, ruotabile, legato al pool) — il worker remoto non vede Postgres né Redis.
- Il worker remoto lavora su una **copia** del progetto: `lease` restituisce un archivio della
  cartella del progetto, `complete` riceve il delta (file cambiati) e l'API lo applica sotto
  lock. Trasporto: HTTPS verso `books.tadamoo.com/api` (nessuna porta aperta sul PC).
- Il PC del titolare esegue `python -m bookwriter.worker --pool local-gpu --remote
  https://books.tadamoo.com/api --token …` accanto a LM Studio (resta valida la regola: il worker
  usa il modello già caricato, non carica né scarica niente).
- Test: lease remoto → esecuzione → delta applicato; conflitto (progetto cambiato nel frattempo)
  → job rifiutato e rimesso in coda.

### Fase 8 — Sicurezza e governance
- Header di sicurezza e CSP nel gateway; rate limit (nginx `limit_req` su `/api`, più stretto su
  `/auth`); antivirus sugli upload (ClamAV come sidecar, opzionale); accesso di assistenza
  temporaneo con audit; esportazione completa dei dati di un tenant (zip) e cancellazione (GDPR);
  rotazione della chiave master (doppia chiave `MultiFernet`).
- Test: CSP non rompe l'app (Playwright); rate limit; audit di ogni azione amministrativa.

### Fase 9 — Deploy su tadamoo
- 🔐 DNS, proxy host NPM, ruoli Postgres, realm `books` (script), segreti.
- `deploy/k8s/books.yaml` + `LEGGIMI.md` + `rilascia.ps1`; build locale e import; rollout.
- Collegamento a uptime-kuma (`/api/health`) e a prometheus (metriche FastAPI con
  `prometheus-fastapi-instrumentator`, scraping via NodePort interno).
- **Fatto quando**: login reale su `books.tadamoo.com`, un tenant di prova con due utenti, un
  libro «Sorpresa» composto con un fornitore cloud, backup notturno eseguito e ripristinato in
  una prova.

### Fase 10 — Dopo la v1
- `project.db` (SQLite) → schema Postgres per progetto (`tenant_<uuid>`), LanceDB → pgvector;
  worker senza disco condiviso (oggetti su MinIO) per scalare su più nodi.
- Fatturazione (Stripe, come tarots), piani self-service, collaborazione in tempo reale sulla
  specifica.

---

## 11. Rischi e risposte

| rischio | risposta |
|---|---|
| un realm in più sull'istanza Keycloak di tarots lega i rilasci | realm importato via Admin REST (non nell'immagine); se serve un tema books o isolamento → D1 alternativa, migrazione con export/import del realm |
| costi dei modelli cloud fuori controllo | quote per piano e per utente, stima prima del job (capitoli × misura), arresto a quota esaurita con messaggio, cruscotto costi in admin |
| un nodo solo con `local-path` | backup notturni verificati (fase 9), RPO 24 h; fase 10 per l'alta disponibilità |
| lavori lunghissimi (ore) e riavvii | il motore è a checkpoint su disco; lease + heartbeat + ripresa |
| fuga di dati fra tenant | tre difese (§6.3) + test tabellare su ogni rotta + RLS |
| il ciclo di correzione della qualità consuma senza fine | tetto dei giri nelle impostazioni, oltre il tetto un giro alla volta su autorizzazione, una versione si tiene solo se il voto sale davvero; consumi sotto la voce `qualita` e quote per piano |
| password create a mano | solo verso Keycloak (mai salvate né registrate), temporanee per impostazione, policy del realm, audit dell'azione senza il valore, rate limit sulle rotte di creazione |
| upload malevoli (pdf, docx) | dimensione massima, MIME verificato, parsing nel worker (mai nell'API), ClamAV opzionale, contenitore senza privilegi (`runAsNonRoot`, `readOnlyRootFilesystem` tranne `/data` e `/tmp`) |

---

## 12. Stato

| fase | stato |
|---|---|
| 0 decisioni D1–D5 | ✅ prese il 25/10/2026 (§2) |
| piano | aggiornato il 25/10/2026: novità del desktop (§1.1), qualità, statistiche, impostazioni di piattaforma via web, utenti creati a mano |
| 1–10 | da fare |
