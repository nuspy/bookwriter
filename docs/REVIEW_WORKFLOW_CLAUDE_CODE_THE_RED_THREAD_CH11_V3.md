# THE RED THREAD — Specifica di review per Claude Code
## Miglioramento del workflow di generazione e dei controlli di qualità

**Tipo di documento:** specifica operativa per il sistema di revisione e rigenerazione locale  
**Capitolo usato come caso pilota:** Chapter 11 — *The Excavation*, bozza V3  
**Lingua del documento:** italiano; il manoscritto resta in American English  
**Obiettivo:** migliorare significativamente la bozza originale tramite il workflow già presente nell’app, quindi valutare separatamente la qualità della review prodotta dall’app e quella della bozza migliorata.

---

## 1. Scopo e contesto d’uso

Questo documento non è una richiesta di riscrivere manualmente il capitolo dall’esterno dell’app. È una specifica perché Claude Code possa migliorare il workflow esistente nel quale l’app:

1. analizza una bozza con un LLM;
2. individua cosa non funziona;
3. decide quali interventi sono necessari;
4. modifica o rigenera localmente il contenuto;
5. dovrebbe verificare che il risultato sia effettivamente migliore e non abbia introdotto regressioni.

La bozza V3 di Chapter 11 è il **caso di prova**. Il suo originale deve prima essere migliorato in modo sostanziale attraverso l’app. Solo dopo si confronteranno la review dell’app, il piano di intervento e il testo migliorato. Non presumere che le funzioni descritte qui siano già implementate: questa è la specifica con cui verificare e migliorare ciò che esiste.

### Principio fondamentale

Il sistema non deve limitarsi a “rendere il testo più bello” o a ridurre parole ripetute. Deve migliorare **causalità, continuità, leggibilità, suspense, funzione delle scene e coerenza del manoscritto**, preservando voce, lunghezza e canon narrativo.

La review deve produrre interventi verificabili. Una modifica è un miglioramento solo se risolve un difetto identificato senza creare un problema uguale o più grave altrove.

---

## 2. Contratto editoriale da trattare come vincolo

Questi vincoli prevalgono sulle preferenze stilistiche generiche del modello.

- **Lingua del manoscritto:** American English.
- **Lunghezza obiettivo:** circa 7.000 parole per capitolo; tolleranza ordinaria ±10%, quindi 6.300–7.700 parole. Non applicare la tolleranza se l’autore ha approvato esplicitamente un’altra lunghezza.
- **Non tagliare aggressivamente per risolvere le ripetizioni.** Se una parte è debole, ridondante o astratta, preferire la sostituzione con una scena più forte, un indizio, un’azione, una decisione o una conseguenza.
- **Genere:** thriller storico/esoterico d’avventura, non saggio di ricerca mascherato da romanzo.
- **Densità narrativa:** soprattutto nei capitoli 6–12, puntare indicativamente a circa il 30% di avventura/indagine/azione concreta, senza trasformare la percentuale in un algoritmo cieco. La funzione della scena conta più del conteggio meccanico.
- **Canon narrativo:** una premessa esoterica o storica inventata può essere vera all’interno della finzione. Non correggerla automaticamente per adeguarla alla storia reale; controllare invece che sia coerente con il canon e che il testo la presenti intenzionalmente.
- **Fonti:** non aggiungere fatti, date, numeri o citazioni bibliografiche al manoscritto senza registrare la provenienza in `research/`. Distinguere nel sistema fra canon, evidenza esterna e ipotesi.
- **Voce e ritmo:** prosa immersiva, brisk, con immagini selettive; evitare spiegazioni reiterate che enunciano la stessa tesi invece di farla avanzare attraverso eventi.
- **Narratore:** il narratore non deve rivelare prima del finale ciò che il canon riserva all’ultima pagina sulla morte di Nicolò.
- **Preservazione del contenuto:** una revisione locale non può cancellare un indizio, cambiare proprietario a un oggetto, anticipare conoscenze o modificare un evento del canon senza registrare la decisione.
- **Nessuna falsa sicurezza:** se il modello non può verificare una continuità o una fonte, deve segnalarla come non verificata, non inventare una soluzione.

---

## 3. Diagnosi iniziale del caso pilota: Chapter 11 V3

Questa sezione riassume i problemi già individuati nella review della bozza V3. Serve come test di accettazione concreto per il workflow; non è un giudizio su una futura versione che non è stata ancora generata.

### 3.1 Problemi critici da risolvere

**A. Cronologia Egitto–Francia incoerente**

La bozza colloca Nicolò al Cairo in serata, menziona un volo e un treno, poi lo colloca in Borgogna poche ore dopo e successivamente alla Gare de Lyon in una sequenza che non torna. Il sistema deve costruire una timeline esplicita prima di riscrivere la prosa. Non può risolvere la contraddizione con frasi vaghe o con una semplice sostituzione di orari.

**B. Continuità incompleta con Chapter 10**

Il capitolo precedente termina con Salma che custodisce il pacchetto di cera, Nicolò che annota “custody” e “Osiris”, l’attesa del proprietario, il ritorno dell’uomo dalla camicia chiara e la fuga sui tetti. La V3 non chiarisce in modo sufficiente:
- che cosa accade a Salma e Yusuf;
- che cosa accade al proprietario atteso;
- chi conserva il pacchetto e con quale autorità;
- perché Nicolò lascia la situazione e si dirige verso l’ufficio/Francia;
- quali informazioni o oggetti porta davvero con sé.

**C. Due scene d’archivio che si duplicano**

“The Conservation Drawer” e “The Filing” ripropongono situazioni, ambienti e informazioni simili: ufficio, archivista/direttore, trasferimento, giurisdizione, filing, Lyon/pubblicazione. Il sistema deve decidere la funzione distinta di ogni scena. Se entrambe non sono necessarie, unirle conservando i dettagli più forti. Se sono entrambe necessarie, la seconda deve introdurre un evento, una prova, una scelta o una conseguenza nuovi e irreversibili.

**D. Continuità e provenienza degli oggetti/documenti**

La bozza presenta sei fiale/campioni residui e un’immagine di Baphomet portati da un corriere; più avanti il direttore parla dei “fragments” come se li avesse portati Nicolò. Nel frattempo la borsa di Nicolò contiene tre quaderni, una chiavetta USB e una fotocopia del 1901. Il pacchetto di Chapter 10 appartiene a Salma, salvo trasferimento esplicito in scena. Il sistema deve distinguere questi oggetti e non trasferirne implicitamente il possesso.

**E. Apertura storica e salto temporale poco leggibile**

“The Steward’s Commission” combina il periodo templare, elementi dell’amministrazione napoleonica e riferimenti a una rete egiziana successiva. La narrazione può usare deliberatamente il filo rosso attraverso tempi diversi, ma il salto deve essere comprensibile e intenzionale. Il sistema non deve “correggere” il canon in automatico: deve prima stabilire se si tratta di una transizione narrativa deliberata o di un anacronismo involontario.

### 3.2 Problemi strutturali importanti

**F. Ripetizione concettuale del tema del filing**

La bozza ripete la stessa tesi — gli oggetti non vengono distrutti, ma registrati/archiviati/trasferiti — in più scene. La frequenza della parola “filing” è salita rispetto alla versione precedente (circa 36 occorrenze in V2, 45 in V3, secondo il conteggio usato nella review). Il problema non è solo lessicale: è la reiterazione della medesima funzione drammatica. Il sistema deve controllare sia la ripetizione delle parole sia quella delle idee e degli eventi.

**G. “The Friday of the Thirteenth” ripete il tema invece di farlo avanzare**

La sezione ripropone l’idea che gli oggetti vengano registrati anziché distrutti. Deve introdurre una conseguenza concreta: una perdita, una scelta irreversibile, un tradimento, un cambio di custodia, una prova che scompare, una minaccia che si materializza o una divisione fra i custodi. Non basta una nuova formulazione della tesi.

**H. Lunghezza fuori target**

La V3 è di circa 8.196 parole nel conteggio usato per la review: circa il 17% oltre il target di 7.000 e circa 496 parole oltre il limite ordinario di 7.700. Il sistema non deve semplicemente tagliare le ultime 500 parole. Deve ridisegnare le scene ridondanti e mantenere o migliorare la densità narrativa.

**I. Miglioramenti parziali da preservare**

Rispetto a V2, la V3 rende più esplicito il ponte documentale dall’Egitto alla giurisdizione francese; Nicolò è più attivo nell’interpretare il documento e prendere decisioni; la minaccia della divulgazione pubblica/conferenza è più concreta; alcune ripetizioni letterali, come “west”, sono diminuite. Questi progressi non vanno persi durante la revisione.

---

## 4. Workflow raccomandato: separare diagnosi, piano, intervento e verifica

L’app può usare un singolo modello o più passaggi, ma deve separare logicamente queste responsabilità. Non chiedere a un solo prompt di “rivedere tutto” senza un piano controllabile.

### Fase 0 — Congelamento e baseline

Prima di modificare:
1. conservare una copia immutata dell’originale;
2. assegnare un ID/versione alla bozza e registrare il modello/prompt di revisione;
3. calcolare word count totale e per sezione;
4. estrarre titoli, scene, personaggi, luoghi, date/orari, oggetti, documenti, eventi e affermazioni di continuità;
5. conservare la review precedente e i suoi rilievi, se presenti.

**Output richiesto:** baseline machine-readable + report leggibile. Mai sovrascrivere l’unica copia originale.

### Fase 1 — Analisi del contesto

Il modello deve consultare, se disponibili:
- capitolo precedente e successivo;
- story bible/canon;
- outline approvato;
- decision log dell’autore;
- registro degli oggetti e delle prove;
- `research/` e fonti già registrate.

Deve distinguere:
- **Canon:** fatti veri nel mondo del romanzo;
- **Testo presente:** ciò che la bozza afferma esplicitamente;
- **Evidenza esterna:** fatti verificati e fonti registrate;
- **Inferenza:** conclusione plausibile ma non esplicita;
- **Domanda aperta:** elemento che non si può risolvere con i dati disponibili.

Non colmare una lacuna di contesto inventando un fatto. Se la soluzione richiede una decisione dell’autore, segnalarla come tale.

### Fase 2 — Diagnosi strutturata

Ogni rilievo deve avere:
- ID stabile, ad esempio `CH11-TIME-001`;
- gravità: **Blocker**, **Major**, **Moderate**, **Minor**;
- categoria;
- evidenza localizzabile (titolo scena e breve citazione o intervallo);
- spiegazione del difetto e del suo impatto sul lettore;
- causa probabile;
- intervento richiesto;
- dipendenze da altre modifiche;
- test verificabile per dichiararlo risolto;
- rischio di regressione.

Non assegnare un rilievo senza un’evidenza nel testo o nel contesto consultato. Non usare formule generiche come “migliorare il ritmo” senza dire dove, perché e come verificare il miglioramento.

### Fase 3 — Piano d’intervento ordinato

Prima della riscrittura, creare un piano con:
1. interventi bloccanti;
2. interventi strutturali;
3. interventi di continuità;
4. interventi su scene e suspense;
5. interventi di stile;
6. controlli finali.

Per ogni intervento indicare se è:
- **Rewrite:** riscrivere una scena;
- **Merge:** unire scene ridondanti;
- **Split:** separare funzioni oggi confuse;
- **Insert:** aggiungere un ponte/scena/azione mancante;
- **Move:** spostare una scena o informazione;
- **Trim:** eliminare ridondanza senza perdere contenuto necessario;
- **Preserve:** elemento da proteggere perché funziona;
- **Author decision:** richiede una scelta dell’autore.

Il piano deve identificare gli elementi da preservare e i rischi, non solo gli errori.

### Fase 4 — Revisione per blocchi

Eseguire gli interventi in ordine di dipendenza:
1. timeline e causalità;
2. continuità fra capitoli;
3. possesso/provenienza di oggetti e documenti;
4. funzione delle scene e ordine delle rivelazioni;
5. ripetizioni concettuali;
6. ritmo e prosa;
7. lunghezza.

Non effettuare una passata stilistica completa prima di aver stabilizzato cronologia, eventi e oggetti. Quando una scena viene riscritta, verificare le scene adiacenti e tutte le occorrenze degli elementi modificati.

### Fase 5 — Verifica indipendente post-revisione

Un passaggio di verifica separato deve confrontare:
- baseline originale;
- rilievi iniziali;
- piano d’intervento;
- testo revisionato;
- invarianti del canon e decision log.

Il verificatore non deve limitarsi a chiedere al modello che ha scritto il testo se ha fatto un buon lavoro. Deve testare ogni rilievo uno per uno e cercare regressioni non previste.

### Fase 6 — Decisione

Esiti possibili:
- **PASS:** tutti i gate obbligatori superati;
- **PASS WITH WARNINGS:** nessun blocker, ma restano questioni non bloccanti esplicitate;
- **REVISE:** problemi correggibili senza decisione dell’autore;
- **AUTHOR INPUT REQUIRED:** una scelta di canon o di trama non può essere dedotta;
- **FAIL:** regressioni critiche o vincoli editoriali violati.

Se un gate fallisce, generare una lista circoscritta di correzioni. Evitare di rigenerare l’intero capitolo per un difetto locale.

---

## 5. Quality rubric: criteri e punteggi

Usare una scala da **0 a 4** per ciascun criterio:

- **0 — Fallimento:** difetto assente di controllo o contraddizione grave.
- **1 — Debole:** difetti frequenti che ostacolano comprensione o immersione.
- **2 — Parziale:** funziona in parte, ma restano problemi sostanziali.
- **3 — Solido:** soddisfa lo standard, con difetti minori.
- **4 — Eccellente:** risultato robusto, coerente e narrativamente efficace.

Il punteggio deve essere accompagnato da evidenza e motivazione. Un punteggio non supportato da esempi è invalido.

| Criterio | Peso | Che cosa verifica |
|---|---:|---|
| Causalità e logica degli eventi | 15% | Ogni evento deriva da una causa leggibile; decisioni e conseguenze sono comprensibili. |
| Cronologia e geografia | 12% | Giorni, ore, fusi orari, spostamenti e distanze sono compatibili. |
| Continuità con il canon e capitoli adiacenti | 12% | Stati dei personaggi, conoscenze, ferite, promesse, oggetti e minacce proseguono coerentemente. |
| Tracciabilità di oggetti/documenti/prove | 10% | Ogni elemento importante ha origine, proprietario/custode, posizione e passaggi di mano chiari. |
| Funzione e distinzione delle scene | 10% | Ogni scena modifica situazione, conoscenza, rischio, relazione o obiettivo; le scene non duplicano la stessa funzione. |
| Suspense, decisioni e conseguenze | 10% | Il protagonista sceglie, agisce e paga un costo; le minacce diventano eventi e non restano solo spiegazioni. |
| Densità narrativa d’avventura | 8% | Indagine, azione, ostacoli e scoperte concrete sono presenti in modo adeguato al genere e alla posizione del capitolo. |
| Ripetizione lessicale e concettuale | 7% | Le ripetizioni sono intenzionali e progressive; non reiterano una tesi senza sviluppo. |
| Voce, stile e leggibilità | 6% | American English naturale, ritmo immersivo, immagini selettive, esposizione controllata. |
| Lunghezza e bilanciamento | 5% | Rispetta target e tolleranza senza tagli meccanici o gonfiaggio. |
| Fonti e distinzione canon/evidenza/ipotesi | 5% | I fatti esterni sono tracciabili; il canon non viene confuso con la storia documentata. |

### Regola di scoring

- Il punteggio complessivo ponderato è utile per confrontare versioni, ma **non può compensare un blocker**.
- Nessuna media può trasformare una cronologia impossibile o una contraddizione di possesso in un PASS.
- Il sistema deve mostrare sia il punteggio iniziale sia quello finale, con motivazioni e rilievi risolti/aperti.
- Per considerare la revisione un miglioramento significativo, il risultato deve migliorare i criteri critici senza peggiorare i vincoli protetti.

---

## 6. Quality gates obbligatori

Questi controlli sono **pass/fail**, non semplici punteggi.

### Gate A — Cronologia

- Ogni spostamento significativo ha punto di partenza, punto di arrivo, durata plausibile e data/ora coerente.
- Specificare il fuso orario quando può cambiare l’interpretazione.
- Nessun personaggio può apparire in due luoghi incompatibili nello stesso intervallo.
- Le indicazioni relative (“la sera seguente”, “il giorno dopo”) devono essere risolvibili rispetto a una data o a un ancoraggio chiaro.
- Se la cronologia non è risolvibile, fermare la riscrittura e segnalare la decisione o il dato mancante.

### Gate B — Continuità fra capitoli

- Confrontare l’inizio del capitolo con lo stato finale del precedente.
- Controllare persone presenti/assenti, oggetti, promesse, minacce, ferite, informazioni e obiettivi.
- Ogni cambiamento di stato rilevante deve avvenire in scena oppure essere spiegato con un ponte breve e credibile.
- Non eliminare un personaggio o una conseguenza aperta solo perché è scomoda per la nuova struttura.

### Gate C — Oggetti e documenti

Per ogni oggetto significativo mantenere un record con:
- ID e nome;
- prima comparsa;
- origine/provenienza;
- proprietario e custode attuale;
- posizione;
- contenuto o funzione;
- passaggi di mano, con luogo/tempo/modalità;
- ultima comparsa;
- stato finale del capitolo.

**Regola:** nessun oggetto può cambiare custode, essere duplicato, sparire o ricomparire senza una transizione testuale o una decisione di canon registrata.

### Gate D — Conoscenze dei personaggi

Per ogni rivelazione importante verificare:
- chi la conosce;
- quando l’ha appresa;
- da chi o da quale prova;
- chi non la conosce ancora;
- se il testo anticipa una conoscenza senza mostrarne la fonte.

Evitare che un personaggio agisca sulla base di un’informazione che non ha acquisito.

### Gate E — Funzione delle scene

Per ogni scena rispondere:
1. Che cosa vuole il protagonista o il personaggio focale?
2. Quale ostacolo si oppone?
3. Che cosa cambia entro la fine?
4. Quale nuova informazione, decisione o conseguenza emerge?
5. Perché la scena deve stare proprio qui?
6. Quale scena adiacente svolge già la stessa funzione?

Se una scena non cambia niente, valutare di unirla, sostituirla o darle una conseguenza concreta. Non eliminare una scena soltanto perché è lenta: prima verificare se può essere resa attiva.

### Gate F — Ripetizione concettuale

Rilevare almeno tre livelli:
1. **Lessicale:** stessa parola o locuzione ripetuta.
2. **Semantico:** formulazioni diverse che esprimono la stessa tesi.
3. **Drammaturgico:** scene diverse che producono lo stesso effetto narrativo.

Per ogni cluster di ripetizioni classificare:
- motif intenzionale con progressione;
- richiamo necessario per comprensione;
- ridondanza da comprimere;
- duplicazione da sostituire con un evento nuovo.

Un conteggio di parole è solo un segnale. Non imporre limiti rigidi a una parola tematica senza leggere il contesto. Per esempio, il motivo del “filing” può essere centrale, ma ogni ricorrenza importante deve aggiungere un significato, una prova, un costo o una conseguenza.

### Gate G — Lunghezza

- Target ordinario: 7.000 parole.
- Fascia accettabile: 6.300–7.700, salvo deroga approvata.
- Misurare sia il capitolo totale sia le singole scene/sezioni.
- Se fuori fascia, spiegare la causa (scene duplicate, esposizione eccessiva, scena mancante, ritmo troppo compresso).
- Vietato tagliare soltanto per rientrare nel limite. Prima intervenire su duplicazioni e spiegazioni; sostituire il materiale debole con narrazione più efficace quando serve.
- Registrare word count prima/dopo e delta percentuale.

### Gate H — Fonti

- Ogni fatto storico esterno aggiunto o modificato deve avere una voce in `research/` con fonte e collegamento al punto del testo.
- Le fonti della bibliografia del manoscritto non sono automaticamente una prova di ogni affermazione.
- Non inventare citazioni o riferimenti.
- Se un dettaglio appartiene al canon, etichettarlo come canon; non presentarlo come fatto storico verificato.
- Se non è possibile verificare, marcare come `UNVERIFIED` e non aggiungere certezza alla prosa.

### Gate I — Vincoli protetti

Verificare:
- American English;
- narratore non onnisciente rispetto alla rivelazione riservata al finale;
- nessuna alterazione non autorizzata del canon;
- nessuna perdita involontaria di indizi, promesse o payoff;
- nessuna modifica fuori scope ad altri capitoli;
- nessuna sovrascrittura della baseline originale.

---

## 7. Metriche e strumenti di controllo

Usare metriche deterministiche dove possibile e LLM per l’interpretazione dove necessario.

### Controlli deterministici consigliati

- word count per capitolo, sezione e scena;
- conteggi e trend di termini selezionati;
- estrazione di date, orari, numeri, luoghi e nomi;
- differenze fra versione precedente e nuova;
- confronto di entità: personaggi, oggetti, documenti, luoghi;
- controllo che ogni rilievo abbia un test e un esito;
- controllo delle fonti citate nel testo rispetto al registro `research/`;
- rilevamento di sezioni duplicate o quasi duplicate, come segnale da verificare e non come eliminazione automatica.

### Controlli LLM

- coerenza causale;
- funzione drammatica delle scene;
- ripetizione semantica e concettuale;
- progressione del motivo tematico;
- voce, ritmo, suspense e leggibilità;
- compatibilità con il canon e con i capitoli adiacenti;
- confronto tra l’intenzione dell’intervento e il risultato effettivo.

### Regola anti-falsa precisione

Non presentare una metrica euristica come verità assoluta. Un rilevatore può segnalare una possibile duplicazione, ma la decisione deve considerare il contesto. Ogni metrica deve avere:
- definizione;
- metodo di calcolo;
- limiti noti;
- soglia, se applicabile;
- tipo di verifica umana/LLM richiesta.

---

## 8. Requisiti del report prodotto dall’app

Ogni esecuzione dovrebbe generare un report persistente con queste sezioni:

1. **Identità dell’esecuzione:** capitolo, versione input/output, data, modello, prompt/configurazione.
2. **Baseline:** word count, sezioni, personaggi/luoghi/oggetti rilevanti.
3. **Verdetto iniziale:** punteggi, blocker, major, warning.
4. **Piano approvato dall’algoritmo:** interventi ordinati e motivazione.
5. **Registro delle modifiche:** che cosa è cambiato, dove, perché e quale rilievo risolve.
6. **Elementi preservati:** scene, indizi, immagini o passaggi che non devono essere persi.
7. **Verifica post-revisione:** esito di ciascun test.
8. **Regressioni:** problemi nuovi introdotti, anche se il testo è migliorato sotto altri aspetti.
9. **Questioni aperte:** informazioni mancanti o decisioni dell’autore necessarie.
10. **Confronto prima/dopo:** rubric, word count, continuità, scene, ripetizioni, fonti.
11. **Verdetto finale:** PASS / PASS WITH WARNINGS / REVISE / AUTHOR INPUT REQUIRED / FAIL.
12. **Prossimo intervento minimo:** elenco circoscritto di modifiche per il prossimo passaggio.

Il report non deve limitarsi a dire “migliorato” o “qualità 8/10”. Deve dimostrare in che modo la versione nuova risolve i problemi, con evidenza testuale.

---

## 9. Come valutare se la revisione ha funzionato

Una revisione è **significativa** quando:
- risolve tutti i blocker identificati;
- risolve o giustifica esplicitamente i problemi major;
- mantiene i punti di forza della V3;
- non introduce contraddizioni nuove;
- porta la lunghezza nella fascia concordata o spiega perché serve una deroga;
- trasforma la ripetizione concettuale in progressione drammatica;
- rende le scene necessarie e distinguibili;
- mantiene il canon e la continuità;
- lascia una traccia verificabile del lavoro svolto.

Non basta:
- ridurre il numero di occorrenze di una parola;
- cambiare sinonimi senza cambiare la funzione della scena;
- aggiungere spiegazioni che dichiarano la soluzione senza metterla in scena;
- comprimere il testo fino al target sacrificando indizi o conseguenze;
- far salire il punteggio medio mentre resta un blocker.

### Confronto prima/dopo

Per ogni rilievo iniziale assegnare uno stato:
- `RESOLVED` — risolto con evidenza;
- `PARTIALLY_RESOLVED` — migliorato ma resta un problema;
- `UNRESOLVED` — non risolto;
- `REGRESSION` — peggiorato o ha causato un problema nuovo;
- `NOT_APPLICABLE` — non più applicabile, con motivazione.

Per ogni stato diverso da `RESOLVED`, includere una spiegazione. Il sistema non può segnare un problema come risolto solo perché il testo è stato modificato.

---

## 10. Piano di revisione specifico per Chapter 11 V3

L’ordine è importante perché i problemi dipendono l’uno dall’altro.

### Priorità 1 — Ricostruire timeline e itinerario

Creare prima una tabella di lavoro, non necessariamente da inserire nel romanzo:

| Evento | Data/giorno | Ora locale | Luogo | Durata/spostamento | Evidenza nel testo | Stato |
|---|---|---|---|---|---|---|
| Fine degli eventi di Chapter 10 | da verificare | da verificare | Cairo | — | Chapter 10 | OPEN |
| Uscita/partenza di Nicolò | da decidere | da decidere | Cairo | da verificare | Chapter 11 | OPEN |
| Volo o trasferimento internazionale | da definire | da definire | Egitto → Francia | plausibilità da verificare | Chapter 11 | OPEN |
| Arrivo in Francia | da definire | da definire | aeroporto/stazione | da definire | Chapter 11 | OPEN |
| Viaggio verso Lyon/Borgogna | da definire | da definire | Francia | da verificare | Chapter 11 | OPEN |
| Incontro/archivio | da definire | da definire | luogo preciso | — | Chapter 11 | OPEN |

Non inventare gli orari per riempire la tabella: derivarli dal testo, dal canon o da una decisione registrata. Se il romanzo richiede un viaggio impossibile, cambiare la sequenza narrativa, non la fisica.

### Priorità 2 — Ripristinare il ponte con Chapter 10

Scrivere un elenco puntuale di:
- persone rimaste al Cairo e loro stato;
- pacchetto di cera e sua custodia;
- campioni/fiale e immagine di Baphomet;
- quaderni, USB e fotocopia del 1901;
- informazioni che Nicolò possiede e quelle che non possiede;
- motivo concreto per cui parte;
- costo o rischio della decisione.

Integrare nel capitolo solo i dettagli necessari al lettore; mantenere il ledger completo come strumento di verifica.

### Priorità 3 — Decidere la funzione delle scene d’archivio

Per “The Conservation Drawer” e “The Filing” definire prima:
- scopo unico;
- informazione nuova;
- conflitto;
- scelta di Nicolò;
- conseguenza;
- oggetti/prove presenti.

Se le due scene svolgono lo stesso lavoro, unirle. Se entrambe restano, la seconda deve cambiare il quadro e non ripetere semplicemente il concetto di filing, Lyon e pubblicazione.

### Priorità 4 — Risolvere il ledger degli oggetti

Tracciare separatamente almeno:
- pacchetto di cera di Chapter 10;
- sei fiale/campioni residui;
- immagine di Baphomet;
- tre quaderni;
- USB;
- fotocopia del 1901;
- transfer slip;
- qualsiasi cartella o numero di conservazione introdotto.

Per ciascuno chiarire origine, custode, luogo, passaggio di mano e ultima comparsa. Non usare “fragments” come riferimento ambiguo se il lettore non può capire a quali oggetti si riferisca.

### Priorità 5 — Dare un’evoluzione al tema del filing

Mantenere il motivo se è parte del canon, ma assegnare alle ricorrenze funzioni diverse. Esempio di progressione possibile, da trattare come schema e non come obbligo:
1. **Indizio:** un documento è stato registrato invece di essere distrutto.
2. **Conflitto:** la registrazione mette Nicolò su una pista.
3. **Costo:** il sistema di registrazione espone qualcuno o rende impossibile nascondere una prova.
4. **Scelta:** Nicolò deve decidere se archiviare, sottrarre, divulgare o proteggere.
5. **Conseguenza:** la scelta cambia chi controlla l’indizio o chi è in pericolo.

Non ripetere la tesi in ogni scena; trasformarla in una catena di eventi.

### Priorità 6 — Rendere intenzionale il salto storico

Determinare se “The Steward’s Commission” è:
- una scena storica autonoma;
- un frammento del narratore/filo rosso;
- una ricostruzione documentale;
- una transizione deliberata fra epoche.

La scelta deve essere riconoscibile al lettore. Controllare la compatibilità con il canon e non introdurre anacronismi involontari. Se l’anacronismo è intenzionale, assicurarsi che sia leggibile come tale e non sembri un errore.

### Priorità 7 — Controllare lunghezza e ritmo dopo le correzioni

Solo dopo aver risolto i problemi strutturali:
- misurare il nuovo word count;
- controllare la lunghezza delle scene;
- rimuovere spiegazioni duplicate;
- sostituire le parti astratte con azione o conseguenze, se serve;
- verificare che il capitolo resti entro 6.300–7.700 parole, salvo deroga.

---

## 11. Test di accettazione per la versione migliorata di Chapter 11

La revisione può essere considerata pronta per la successiva valutazione solo se tutti i test seguenti hanno esito PASS oppure hanno una deroga esplicita registrata.

- [ ] La timeline Cairo–Francia–Lyon/Borgogna è coerente e leggibile.
- [ ] Il ponte con la fine di Chapter 10 chiarisce le conseguenze necessarie senza un riassunto eccessivo.
- [ ] Il destino di Salma, Yusuf, proprietario e pacchetto è coerente con il canon o esplicitamente lasciato aperto in modo intenzionale.
- [ ] Il possessore di ciascun oggetto importante è chiaro a ogni passaggio.
- [ ] “The Conservation Drawer” e “The Filing” hanno funzioni distinte oppure sono state fuse senza perdita di materiale necessario.
- [ ] “The Friday of the Thirteenth” produce una conseguenza nuova, non soltanto una nuova formulazione del tema.
- [ ] Il motivo del filing progredisce in modo drammatico; le ripetizioni residue sono intenzionali.
- [ ] La scena storica iniziale è chiaramente incorniciata e coerente con il canon.
- [ ] La lunghezza è entro il target ordinario o esiste una deroga motivata.
- [ ] Nessun indizio, payoff o vincolo protetto è stato perso.
- [ ] Nessun fatto esterno nuovo è stato aggiunto senza traccia in `research/`.
- [ ] Ogni rilievo iniziale ha uno stato e un’evidenza verificabile.
- [ ] Il verificatore ha cercato regressioni nuove, non soltanto controllato la lista iniziale.
- [ ] Il testo finale è prosa narrativa in American English, non un report travestito da capitolo.

---

## 12. Requisiti per il sistema di auto-revisione dell’app

Questi sono requisiti di comportamento, non indicazioni su una particolare architettura software.

1. **Preservare la baseline.** Ogni iterazione deve creare una nuova versione, non distruggere l’originale.
2. **Usare interventi mirati.** Se un difetto riguarda due scene, non rigenerare il capitolo intero salvo dipendenze dimostrate.
3. **Portare il contesto pertinente.** Una modifica alla custodia di un oggetto deve attivare il controllo di tutte le sue occorrenze.
4. **Mantenere ID dei rilievi.** I problemi non devono scomparire dal report solo perché il testo è stato riscritto.
5. **Verificare dopo ogni passaggio strutturale.** Se una scena viene spostata o fusa, rieseguire i gate di cronologia, continuità, oggetti e conoscenze.
6. **Separare scrittura e giudizio.** Il modello che ha scritto la revisione non deve essere l’unica autorità che la approva.
7. **Limitare i cicli.** Ogni ciclo deve avere un obiettivo, un budget di tentativi e una condizione d’arresto. Evitare riscritture infinite che cambiano continuamente il testo.
8. **Mostrare i trade-off.** Se una correzione risolve un problema ma peggiora un altro criterio, registrarlo.
9. **Richiedere input quando necessario.** Non inventare una decisione di canon per evitare di fermarsi.
10. **Tracciare le modifiche.** Ogni cambiamento sostanziale deve essere collegato a un rilievo o a un obiettivo editoriale.
11. **Non ottimizzare una sola metrica.** Il conteggio delle parole, le ripetizioni o il punteggio medio non devono dominare la qualità narrativa.
12. **Fornire un diff leggibile.** L’autore deve poter capire quali passaggi sono cambiati e perché.
13. **Non dichiarare “risolto” senza prova.** Richiedere evidenza testuale o un test deterministico.
14. **Evitare il drift.** La revisione deve mantenere i fatti narrativi e lo stile approvati, anche dopo più iterazioni.
15. **Conservare gli avvisi.** Le incertezze non risolte devono restare visibili nel report finale.

---

## 13. Schema minimo dei dati di review

L’app può usare il formato interno già esistente. Se manca uno schema equivalente, ogni rilievo dovrebbe almeno rappresentare questi campi:

- `issue_id`
- `chapter_id`
- `source_version`
- `category`
- `severity`
- `location`
- `evidence`
- `diagnosis`
- `reader_impact`
- `proposed_action`
- `dependencies`
- `acceptance_test`
- `status`
- `resolution_evidence`
- `regression_risk`

Ogni intervento:
- `change_id`
- `related_issue_ids`
- `operation_type`
- `target_scene_or_range`
- `intended_effect`
- `protected_content`
- `before_after_summary`
- `verification_result`

Ogni run:
- `run_id`
- `input_version`
- `output_version`
- `model_and_configuration`
- `rubric_scores_before_after`
- `word_count_before_after`
- `gate_results`
- `open_questions`
- `final_verdict`

Questo è uno schema concettuale: non imporre una migrazione di database se l’app dispone già di strutture equivalenti. Prima mappare i concetti sui dati esistenti.

---

## 14. Cosa fare adesso, in ordine

1. Esaminare il workflow già implementato nell’app e mappare le sue fasi rispetto alle sezioni 4, 6, 8 e 12.
2. Identificare quali controlli esistono già, quali sono parziali e quali mancano. Non duplicare funzioni equivalenti.
3. Applicare la diagnosi V3 come test concreto: timeline, continuità con Chapter 10, scene d’archivio, ledger degli oggetti, ripetizione concettuale, salto storico e lunghezza.
4. Far generare all’app un piano d’intervento esplicito prima di modificare la bozza.
5. Eseguire la revisione localmente, preservando la V3 come baseline.
6. Eseguire la verifica indipendente e produrre il report prima/dopo.
7. Conservare sia il report della review sia il capitolo migliorato: saranno valutati separatamente.
8. Non procedere subito a una seconda riscrittura completa se il primo risultato non passa. Prima identificare con precisione quali gate falliscono.

### Output attesi dal prossimo ciclo

- una nuova versione del capitolo, senza sovrascrivere la V3;
- un report di review iniziale;
- un piano d’intervento;
- un report di verifica post-revisione con esito per ogni gate;
- un diff o changelog;
- un elenco di questioni ancora aperte o che richiedono decisione dell’autore.

---

## 15. Criterio finale

**La qualità del sistema si misura dalla sua capacità di trasformare una diagnosi in un miglioramento narrativo verificabile, preservando il canon e senza introdurre regressioni.**

La bozza migliorata non deve solo essere più corretta: deve essere più chiara, più causale, più avvincente e più concreta, con scene che fanno avanzare la storia. Il sistema deve saper dimostrare cosa ha corretto, cosa ha preservato, cosa resta irrisolto e quali rischi ha introdotto.

Questo documento definisce il comportamento atteso. Non certifica che l’app implementi già questi controlli e non sostituisce la successiva valutazione dell’output reale generato dall’app.
