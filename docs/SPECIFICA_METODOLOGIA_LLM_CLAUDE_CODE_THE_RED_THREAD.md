# Specifica funzionale e workflow
## Motore di interpretazione editoriale LLM, metodologia adattiva e controllo delle revisioni
### Documento operativo per Claude Code — progetto *The Red Thread*

**Scopo:** trasformare l'analisi editoriale da una lista generica di osservazioni in un processo verificabile che individui i requisiti specifici dell'opera, costruisca una metodologia adatta, guidi la pianificazione e la stesura, blocchi gli errori strutturali noti e verifichi che le riparazioni siano effettive.

**Principio guida:** l'LLM decide che cosa è rilevante per questo testo e interpreta significato, causalità e funzione narrativa; il codice controlla in modo deterministico ciò che può essere formalizzato; l'autore mantiene l'autorità su canon, intenzione e decisioni ambigue.

---

# 1. Sintesi esecutiva

Il caso del capitolo 11, versione V4, mostra che la diagnosi preliminare dell'app aveva già individuato molti dei problemi successivamente riscontrati nella review: aritmetica dei 14 nodi, cronologia Cairo–Parigi, scene storiche ripetitive, tre scene contemporanee che ripetono la stessa azione archivistica e motivi lessicali che non fanno avanzare la storia. La stesura è tuttavia proseguita e ha conservato o introdotto incoerenze.

Il problema prioritario non è quindi semplicemente ottenere una diagnosi più intelligente. È rendere affidabile l'intera catena:

1. interpretare l'opera e il suo scopo;
2. generare una metodologia specifica per quell'opera;
3. individuare e classificare i difetti;
4. definire test di accettazione per ogni difetto bloccante;
5. riparare;
6. verificare deterministicamente ciò che è verificabile;
7. sottoporre il risultato a una valutazione narrativa indipendente;
8. impedire l'accettazione se rimangono blocker o sono state introdotte regressioni.

Non usare il semplice numero di difetti rimasti come metrica di successo. Una riparazione è accettabile se risolve i problemi gravi senza violare il canon, i vincoli espliciti o altri requisiti, e se supera i test di accettazione e di regressione.

## Decisioni di progettazione

- Separare un **motore metodologico generale** da un **profilo persistente per ciascun progetto**.
- Generare criteri, pesi, registri, invarianti e rischi in base al tipo di opera, al genere, allo scopo editoriale e al canon.
- Non presumere in anticipo tutte le entità da tracciare: farle scoprire dall'analisi e poi formalizzarle.
- Distinguere blocker, difetti maggiori, difetti minori e preferenze stilistiche.
- Bloccare la stesura quando esistono blocker irrisolti, salvo deroga esplicita e registrata dell'autore.
- Usare ID canonici e fonti autorevoli per la ricerca; la corrispondenza approssimativa non deve correggere silenziosamente riferimenti ambigui.
- Riconoscere concetti narrativi indipendentemente dalla lingua in cui sono etichettati.
- Controllare ogni riparazione con test espliciti e verifica di regressione.
- Per la continuità del capitolo 11, consultare gli originali presenti nell'app. I file esterni con suffissi come `revised` non sono canonici per il workflow dell'app e non devono essere usati per confermare la continuità.

---

# 2. Diagnosi del problema attuale

Il caso V4 indica almeno quattro classi di guasto.

## 2.1 Diagnosi valida, esecuzione non vincolata

L'analisi può individuare correttamente un problema, ma la generazione parte comunque. La presenza di una diagnosi non garantisce che il piano sia stato riparato.

**Requisito:** una diagnosi deve produrre uno stato operativo verificabile, non solo un elenco testuale. Ogni difetto deve avere gravità, evidenza, stato, criterio di risoluzione e conseguenza sul gate.

## 2.2 Riparazioni valutate con la metrica sbagliata

Contare i difetti prima e dopo è inaffidabile: un LLM può suddividere un problema in più rilievi, accorparli o riformularli. Una riparazione utile può sembrare peggiore solo perché la nuova analisi ha granularità diversa.

**Requisito:** confrontare il rischio residuo per severità e impatto, usando identità stabili dei difetti quando possibile. Una riduzione dei difetti minori non può compensare un nuovo blocker o una regressione critica.

## 2.3 Errori di tracciabilità e riconoscimento

- Un ID di ricerca può essere alterato, per esempio `Kbc7689cb55d` invece di `Kb7689cb55d`.
- I passi di una catena narrativa possono essere scritti in inglese (`consequence`, `choice`) mentre il controllo cerca le etichette italiane.
- Un controllo letterale può quindi dichiarare assente un concetto presente, oppure accettare una corrispondenza sbagliata.

**Requisito:** ID risolti tramite registro autorevole; concetti semantici rappresentati con identificativi stabili e alias multilingue.

## 2.4 Il testo contiene entità e stati che lo schema non aveva previsto

Il conteggio delle schede, i nodi, i fusi orari e le transizioni di custodia sono emersi dal contenuto. Uno schema statico non può anticipare tutti gli oggetti che un'opera può rendere narrativamente rilevanti.

**Requisito:** prima della revisione, un LLM deve proporre i registri specifici del testo, gli invarianti e le verifiche necessarie. Queste proposte devono essere validate e poi eseguite come metodologia versionata.

---

# 3. Architettura proposta

Separare i componenti logici. Non è necessario implementare tutto come servizi separati: possono essere moduli distinti nello stesso processo, purché responsabilità, input e output siano separati.

## Livello A — Profilo dell'opera

Descrive che tipo di oggetto si sta creando e quale esperienza deve offrire.

Campi consigliati:
- tipo di opera/pubblicazione;
- genere e sottogenere;
- pubblico previsto;
- scopo e promessa al lettore;
- lingua, registro e mercato editoriale;
- lunghezza e struttura attese;
- requisiti obbligatori;
- esclusioni e limiti;
- criteri di successo espliciti dell'autore.

Esempi di tipo di opera: romanzo, saggio accademico, manuale, biografia, antologia, narrativa breve, guida tecnica.

Esempi di genere/sottogenere: thriller storico, fantasy, giallo, avventura esoterica, divulgazione scientifica, manuale operativo, biografia critica.

Il tipo di opera e il genere non sono sinonimi. Un romanzo storico e un saggio storico possono trattare lo stesso periodo, ma richiedono criteri diversi.

## Livello B — Profilo persistente del progetto

Contiene le decisioni specifiche dell'autore e il canon che non deve essere reinventato a ogni analisi.

Per *The Red Thread*, includere almeno:
- romanzo di circa 105.000 parole, 15 capitoli;
- American English;
- thriller storico/esoterico con mistero, avventura, ricerca e rivelazioni progressive;
- la narrazione deve essere un'avventura, non un saggio di ricerca mascherato;
- obiettivo indicativo: circa il 30% di ciascun capitolo, soprattutto nei capitoli 6–12, deve essere composto da avventura, investigazione concreta, azione o conseguenze osservabili;
- lunghezza del capitolo: circa 7.000 parole, normalmente entro ±10%, salvo approvazione esplicita;
- non ridurre drasticamente la lunghezza per eliminare ripetizioni: sostituire materiale debole con scene, indizi, decisioni, azione e conseguenze;
- mantenere selettiva l'immagine stilistica e immersiva la prosa;
- distinguere canon narrativo, evidenza esterna e ipotesi;
- non inserire nel manoscritto fatti, numeri o citazioni senza un riferimento registrato in `research/`;
- il narratore è il filo rosso, non onnisciente sulla morte di Nicolò fino all'ultima pagina;
- le premesse esoteriche stabilite come canon possono essere vere all'interno della finzione anche quando divergono dalla ricostruzione storica esterna;
- le scene devono far avanzare personaggio, indagine, pericolo o conseguenze.

**Regola di continuità:** per verificare il capitolo 11 contro il capitolo 10, leggere le versioni originali presenti nell'app. Non usare i file esterni denominati `revised` come sostituti del canon dell'app. Se gli originali non sono accessibili, registrare la verifica come *non eseguita*, non come superata.

## Livello C — Criteri generali di qualità

Si applicano a quasi tutte le opere, ma possono avere pesi diversi:
- coerenza interna;
- chiarezza e leggibilità;
- struttura e progressione;
- precisione e tracciabilità delle affermazioni;
- assenza di contraddizioni;
- adeguatezza al pubblico;
- rispetto dei requisiti espliciti;
- qualità linguistica;
- gestione delle fonti, quando pertinenti.

## Livello D — Criteri specifici del tipo di opera e del genere

Il motore seleziona e adatta i criteri al profilo.

Esempi:
- **Saggio accademico:** domanda di ricerca, metodo, fonti, argomentazione, riproducibilità, limiti.
- **Manuale:** procedure eseguibili, prerequisiti, ordine operativo, casi di errore, verificabilità.
- **Biografia:** cronologia, provenienza delle fonti, distinzione tra fatti e interpretazioni, completezza dei passaggi decisivi.
- **Antologia:** coerenza editoriale, equilibrio, varietà, criteri di inclusione, transizioni.
- **Romanzo/thriller:** obiettivi, ostacoli, decisioni, indizi, escalation, causalità, suspense, payoff, trasformazione del personaggio, conseguenze.
- **Fantasy o narrativa esoterica:** regole del mondo, limiti del soprannaturale, coerenza del canon, rivelazioni graduali, costi e conseguenze delle capacità o conoscenze.
- **Romanzo storico:** cronologia, plausibilità del contesto, separazione tra realtà documentata e licenza narrativa, coerenza delle tecnologie e dei comportamenti del periodo.

## Livello E — Metodologia specifica generata per il testo

Prima dell'analisi o della stesura, un LLM legge:
1. profilo dell'opera;
2. profilo del progetto;
3. testo o piano corrente;
4. quaderno di ricerca e fonti canoniche;
5. obiettivo del passaggio di lavoro.

Produce una metodologia versionata contenente:
- criteri applicabili e motivazione;
- pesi e soglie proposti;
- registri da tenere;
- invarianti da verificare;
- motivi e ricorrenze da sottoporre ad audit;
- fonti canoniche da consultare;
- rischi specifici;
- test di accettazione;
- condizioni di blocco;
- elementi incerti che richiedono decisione dell'autore.

La metodologia generata è una **proposta**, non una nuova autorità. Deve rispettare i requisiti espliciti e il canon. I conflitti non risolti devono essere segnalati, non inventati o appianati.

## Livello F — Estrattore strutturato

Un LLM estrae dal piano o dal manoscritto i dati pertinenti per ogni scena, capitolo o segmento:
- identificativi di scena e ordine;
- tempo, luogo e fuso orario, se rilevanti;
- personaggi presenti e conoscenze disponibili;
- oggetti, custodia e trasferimenti;
- quantità e categorie;
- indizi introdotti, trasformati o risolti;
- obiettivo, ostacolo, scelta e conseguenza;
- ricorrenze dei motivi e funzione narrativa;
- affermazioni di ricerca e relativi ID;
- stato prima/dopo degli elementi tracciati.

L'estrazione deve includere citazioni o intervalli del testo come evidenza. Se il modello non può stabilire un valore, deve restituire `unknown` o `ambiguous`, non colmare il vuoto con un'ipotesi non dichiarata.

## Livello G — Validatore deterministico

Il codice controlla le proprietà formalizzabili:
- somme e conteggi;
- unicità e validità degli ID;
- transizioni di stato;
- bilanci di entrata/uscita;
- orari e fusi;
- sequenze temporali e durate, quando i dati sono sufficienti;
- lunghezza del capitolo;
- presenza dei campi richiesti;
- corrispondenza dei riferimenti a fonti;
- test di accettazione definiti dalla metodologia.

Il codice non deve decidere se una scena è emozionante o se una scelta del personaggio è drammaticamente convincente: queste sono valutazioni semantiche, da affidare a un'analisi LLM strutturata e motivata.

## Livello H — Valutatore narrativo indipendente

Un passaggio distinto valuta:
- se la scena cambia davvero la situazione;
- se la ripetizione aggiunge informazioni, pressione, costo o conseguenze;
- se il protagonista agisce e prende decisioni;
- se gli indizi sono comprensibili e producono payoff;
- se l'esposizione rallenta l'avventura;
- se il finale produce un cambiamento concreto;
- se la revisione ha preservato voce, ritmo e lunghezza.

Il valutatore deve ricevere i requisiti e le evidenze, non soltanto un punteggio globale. Quando possibile, separare l'LLM che propone la riparazione da quello che la valuta.

---

# 4. Metodologia adattiva: come deve funzionare

## Fase 1 — Interpretazione

L'LLM interpreta il testo alla luce dello scopo editoriale e del canon. Deve rispondere, con evidenze:
- che cosa deve ottenere questo testo?
- che cosa deve sperimentare il lettore?
- quali elementi devono restare invarianti?
- quali elementi sono incerti?
- quali rischi sono specifici di questa opera?

## Fase 2 — Scoperta dei registri

Il modello individua le entità che richiedono tracciamento. Esempi:
- oggetti e loro custodia;
- personaggi e conoscenze;
- luoghi, date, orari e fusi;
- documenti, schede e quantità;
- nodi di una rete;
- indizi e loro stato;
- minacce, decisioni e conseguenze;
- ricorrenze di un motivo narrativo.

Ogni registro deve specificare:
- nome e scopo;
- unità o definizione dell'entità;
- stato iniziale, se noto;
- eventi che modificano lo stato;
- fonte del dato;
- invarianti;
- livello di certezza;
- test di accettazione;
- comportamento in caso di dati mancanti.

Non ogni entità menzionata richiede un registro. Va tracciata quando una sua incoerenza potrebbe rompere la logica, la continuità, la comprensione o il payoff.

## Fase 3 — Invarianti

Gli invarianti devono essere formulati in modo testabile. Esempi del capitolo 11:
- ogni distribuzione disgiunta dei nodi deve sommare al totale canonico di 14;
- per un registro di schede, `presenti + rimosse = iniziali`, se le categorie sono esaustive e mutuamente esclusive;
- un oggetto non può trovarsi simultaneamente in due luoghi incompatibili, salvo che il testo stabilisca copie o duplicati;
- una scena non può usare conoscenze che il personaggio non ha ancora acquisito;
- gli orari devono rispettare il fuso locale e la durata plausibile degli spostamenti;
- un ID di ricerca usato nel testo deve risolversi a un record esistente;
- una ripetizione di un motivo deve avere una funzione definita, non soltanto riprodurre la stessa spiegazione.

Gli invarianti devono riflettere le regole effettive del testo. Se le categorie dei nodi possono sovrapporsi, la somma semplice non è valida: la metodologia deve dichiarare la regola e il validatore deve controllare quella regola, non una supposizione.

## Fase 4 — Audit dei motivi

Per ciascun motivo rilevante, la metodologia definisce:
- che cosa significa;
- quali varianti linguistiche o immagini possono esprimerlo;
- quale funzione ha a ogni ricorrenza;
- quale cambiamento deve produrre;
- quali ripetizioni sono intenzionali;
- quali ripetizioni sono ridondanti.

Per *The Red Thread*, motivi come `filing`, `pattern`, `network` e concetti equivalenti devono essere riconosciuti semanticamente anche quando sono espressi in inglese, italiano o con parafrasi. Non basta contare le occorrenze: occorre verificare che cosa cambia a ogni ricorrenza.

Possibili funzioni da distinguere:
- introduzione di un concetto;
- nuova prova;
- reinterpretazione;
- ostacolo;
- scelta;
- conseguenza;
- escalation;
- payoff;
- ripetizione senza avanzamento.

## Fase 5 — Diagnosi strutturata

Ogni rilievo deve avere almeno:
- ID stabile del difetto;
- categoria;
- gravità;
- evidenza testuale;
- spiegazione del danno prodotto;
- ambito interessato;
- requisito o invariante violato;
- proposta di riparazione;
- test di accettazione;
- rischio di regressione;
- stato: aperto, in riparazione, verificato, respinto, richiede autore.

Usare le stesse identità dei difetti quando la diagnosi successiva si riferisce allo stesso problema. Non confrontare semplicemente il numero di righe dei due report.

---

# 5. Gravità, gate e decisioni

Adottare almeno quattro livelli:

- **Blocker:** contraddizione che rende impossibile una stesura coerente, violazione del canon, timeline incompatibile, conteggio strutturale impossibile, fonte/ID essenziale non risolvibile, oppure continuità decisiva non verificata.
- **Maggiore:** indebolisce significativamente causalità, comprensione, suspense, progressione, caratterizzazione o credibilità.
- **Minore:** problema locale di chiarezza, ritmo, formulazione o transizione.
- **Preferenza:** scelta stilistica non obbligatoria o questione di gusto.

## Regole del gate pre-stesura

1. Nessun blocker noto può essere ignorato silenziosamente.
2. Se un blocker può essere risolto senza modificare il canon, il sistema propone una riparazione mirata e la verifica.
3. Se la riparazione richiede una scelta autoriale, il processo si ferma e chiede una decisione.
4. Una deroga è possibile solo se l'autore la approva esplicitamente e viene registrata con motivazione.
5. Se un dato necessario è sconosciuto, non trasformare l'incertezza in un fatto.
6. I difetti minori non devono impedire ogni stesura: il gate deve essere proporzionato alla gravità.

## Valutazione della riparazione

Una riparazione è accettabile quando:
- risolve il difetto originale secondo il test di accettazione;
- non introduce un nuovo blocker;
- non viola il canon o un vincolo esplicito;
- non crea regressioni in altri registri o capitoli;
- preserva lunghezza, voce e funzione narrativa entro i limiti concordati;
- lascia una traccia della modifica e del risultato della verifica.

**Regola fondamentale:** un nuovo blocker non può essere compensato dalla rimozione di molti difetti minori. Il conteggio totale dei difetti non è la funzione obiettivo.

---

# 6. ID di ricerca, fonti e canon

## Risoluzione degli identificativi

Mantenere un registro autorevole degli ID di ricerca. Ogni ID presente nel piano o nel manoscritto deve essere validato contro quel registro.

Se un ID non esiste:
1. cercare candidati simili;
2. confrontare il contesto e i metadati;
3. se esiste un'unica corrispondenza ad alta confidenza, proporla e registrare la risoluzione;
4. se la corrispondenza è ambigua, fermarsi e chiedere conferma;
5. non riscrivere silenziosamente un ID basandosi soltanto sulla distanza tra stringhe.

La correzione di `Kbc7689cb55d` in `Kb7689cb55d` è un esempio di possibile risoluzione, ma deve avvenire perché il registro autorevole conferma l'identità, non perché le stringhe sono simili.

## Tipi di verità

Separare:
- **Canon narrativo:** ciò che è vero nel mondo del romanzo.
- **Evidenza esterna:** ciò che è supportato da fonti documentate.
- **Ipotesi interpretativa:** una lettura plausibile ma non dimostrata.
- **Licenza narrativa:** una scelta intenzionale che diverge dalla ricostruzione esterna.
- **Dato non verificato:** elemento che richiede ricerca o decisione.

Il sistema non deve correggere il canon solo perché una premessa esoterica diverge dalla storia accademica. Deve però impedire che un'affermazione storica introdotta come fatto sia priva del riferimento previsto nel quaderno di ricerca.

---

# 7. Piano pratico di implementazione

Implementare in passi incrementali, mantenendo il sistema esistente funzionante.

## Priorità 0 — Sicurezza del workflow

Obiettivo: impedire che la stesura proceda con blocker noti.

- Rendere strutturati gli esiti dell'analisi.
- Introdurre gravità, stato e test di accettazione.
- Implementare un gate pre-stesura.
- Registrare ogni deroga dell'autore.
- Bloccare il passaggio se restano blocker non risolti o non esplicitamente derogati.

**Criterio di completamento:** un piano con un blocker noto non può passare alla generazione senza riparazione verificata o deroga registrata.

## Priorità 1 — Registro autorevole e validazione deterministica

- Validare gli ID di ricerca contro il registro.
- Implementare controlli per i registri effettivamente scoperti dalla metodologia.
- Separare dati estratti, dati confermati e ipotesi.
- Produrre messaggi di errore specifici con riferimento alla scena e all'evidenza.

**Criterio di completamento:** gli errori aritmetici, gli ID non validi e le transizioni impossibili sono rilevati senza affidarsi al giudizio generativo dell'LLM.

## Priorità 2 — Valutazione delle riparazioni e regressioni

- Confrontare ogni difetto tramite ID stabile.
- Valutare il rischio residuo per gravità e impatto.
- Eseguire test di accettazione dopo la riparazione.
- Eseguire controlli di regressione sugli invarianti e sui requisiti già superati.
- Conservare un changelog delle riparazioni.

**Criterio di completamento:** il sistema non accetta una riparazione solo perché il report contiene meno difetti.

## Priorità 3 — Metodologia adattiva per tipo di opera

- Implementare profilo dell'opera e profilo persistente del progetto.
- Generare criteri specifici per tipo, genere e scopo.
- Far proporre registri, invarianti, motivi, fonti e rischi.
- Validare la metodologia prima di usarla.
- Versionare la metodologia e conservare il motivo delle sue modifiche.

**Criterio di completamento:** la stessa infrastruttura può analizzare un romanzo, un manuale o un saggio con rubriche differenti senza confondere i rispettivi obiettivi.

## Priorità 4 — Audit semantico e multilingue

- Stabilire ID canonici per i concetti narrativi.
- Mantenere alias, sinonimi e varianti linguistiche.
- Analizzare la funzione delle ricorrenze, non solo la frequenza.
- Separare il giudizio semantico dal controllo deterministico.

**Criterio di completamento:** `consequence`, `choice` e le equivalenti espressioni italiane sono mappate agli stessi concetti quando il contesto lo giustifica.

## Priorità 5 — Valutazione narrativa indipendente

- Introdurre un passaggio separato per causalità, progressione, azione, suspense, payoff e conseguenze.
- Richiedere evidenze e motivazioni per ogni rilievo.
- Verificare lunghezza e preservazione della voce.
- Usare casi di test già noti, incluso il capitolo 11 V4.

**Criterio di completamento:** il sistema distingue un problema meccanico da uno narrativo e non usa un unico punteggio opaco per entrambi.

---

# 8. Workflow operativo completo

1. **Caricamento del contesto:** profilo opera, profilo progetto, canon, quaderno di ricerca, testo originale e obiettivo della sessione.
2. **Interpretazione:** identificare promessa al lettore, requisiti, rischi e vincoli.
3. **Generazione della metodologia:** criteri, pesi, registri, invarianti, audit dei motivi e test.
4. **Validazione della metodologia:** verificare conflitti con il canon, campi mancanti e test non misurabili.
5. **Estrazione strutturata:** analizzare il piano o il manoscritto e collegare ogni valore a un'evidenza.
6. **Validazione deterministica:** eseguire i controlli formali.
7. **Valutazione narrativa:** verificare funzione delle scene, causalità e progressione.
8. **Report dei difetti:** gravità, evidenze, test, proposte e stato.
9. **Gate:** decidere se il piano può procedere.
10. **Riparazione mirata:** modificare solo ciò che è necessario, rispettando i vincoli di lunghezza e canon.
11. **Test di accettazione:** verificare che ogni problema dichiarato risolto sia effettivamente risolto.
12. **Test di regressione:** ripetere i controlli già superati e verificare le sezioni dipendenti.
13. **Revisione indipendente:** cercare errori introdotti dalla riparazione o dalla stesura.
14. **Decisione finale:** accettare, riparare ancora oppure chiedere una decisione dell'autore.
15. **Persistenza:** salvare metodologia, risultati, decisioni, deroghe, versioni e changelog.

---

# 9. Caso di prova: capitolo 11 V4 di *The Red Thread*

La V4 è un test realistico per verificare l'architettura. Non trattare la lista seguente come una metodologia universale; usarla come set iniziale di test del progetto.

## 9.1 Cronologia Cairo–Parigi

Problema osservato: orari di partenza e arrivo, durata del volo, orari di arrivo in città e cambi di giorno non sono coerenti o sufficientemente espliciti.

Test:
- stabilire data e ora locale di ogni evento;
- indicare i fusi pertinenti;
- verificare durata del volo e trasferimento;
- verificare che l'ordine degli eventi sia possibile;
- rendere esplicito il passaggio della notte non raccontata se necessario.

Non inventare la soluzione senza verificare il canon e le scene originali.

## 9.2 Registro delle schede archivistiche

Problema osservato: conteggi e stati delle schede sembrano contraddirsi: 11, 14 e 8, con riferimenti a schede già rimosse o trasferite prima che il personaggio arrivi.

Test:
- stabilire il conteggio iniziale, se documentato;
- registrare ogni rimozione, trasferimento o scoperta con tempo, agente e fonte;
- distinguere schede, riferimenti e nodi: non trattarli come la stessa entità;
- verificare che ogni stato successivo derivi dagli eventi precedenti;
- segnalare i valori non ricostruibili come ambigui.

## 9.3 Registro dei 14 nodi

Problema osservato: una distribuzione indicata come 9 + 5 + 2 somma 16, mentre il totale dichiarato è 14. In un'altra distribuzione, i sottogruppi non sono compatibili con i conteggi precedenti.

Test:
- definire se i gruppi sono disgiunti o sovrapposti;
- stabilire il totale canonico;
- creare un registro unico dei nodi con categoria e stato;
- confrontare ogni riepilogo con il registro;
- non “correggere” i numeri in automatico se non è chiaro quale categoria o dato sia errato.

## 9.4 Scene contemporanee nell'archivio

Problema osservato: tre scene nella stessa stanza riservata svolgono in gran parte la stessa azione.

Test semantico per ogni scena:
- che cosa sa Nicolò all'inizio e che cosa sa alla fine?
- quale decisione prende?
- quale ostacolo nuovo affronta?
- quale costo o conseguenza emerge?
- che cosa cambia nello stato dell'indagine o della minaccia?

Se due scene hanno la stessa funzione, proporre di unirle o differenziarle, senza tagliare indiscriminatamente la lunghezza richiesta.

## 9.5 Scene storiche e ripetizione del 1312/1317

Problema osservato: scene diverse presentano il 1312 e il 1317 come “the final filing” con contenuto molto simile; la sequenza storica salta tra epoche e presente.

Test:
- attribuire a ogni scena storica una funzione distinta;
- verificare se introduce prova, cambiamento, conseguenza o reinterpretazione nuova;
- mantenere salti temporali intenzionali ma rendere leggibili le transizioni;
- evitare che una scena ripeta la spiegazione già fornita senza modificare la comprensione del lettore.

## 9.6 Audit dei motivi

Problema osservato: parole come `filing` e `pattern` ricorrono molto, ma la ripetizione lessicale è soltanto un indizio, non la prova definitiva di ridondanza.

Test:
- identificare tutte le ricorrenze rilevanti, incluse le parafrasi;
- attribuire una funzione narrativa a ciascuna;
- verificare progressione o cambiamento;
- segnalare le ricorrenze che ripetono lo stesso concetto senza nuova informazione o pressione;
- controllare sia i termini inglesi sia le etichette italiane usate nella metodologia.

## 9.7 Finale e conseguenza irreversibile

Problema osservato: il piano non sembra aver identificato chiaramente il rischio di un finale che resta concettuale o amministrativo senza una conseguenza concreta.

Test:
- individuare che cosa cambia irreversibilmente per Nicolò o per l'indagine;
- verificare se la conseguenza è mostrata in scena e non solo dichiarata;
- verificare che derivi causalmente dalle sue azioni o dalla scoperta;
- se l'autore intende deliberatamente un finale diverso, registrare quella scelta nel profilo del progetto.

## 9.8 Continuità con il capitolo 10

La verifica deve consultare il capitolo 10 originale presente nell'app. In particolare, controllare:
- che cosa è stato consegnato a Nicolò a Dendera, se questo è ciò che dice l'originale;
- stato e custodia del pacchetto di cera;
- stato di Salma e Yusuf;
- quali informazioni Nicolò possiede;
- ragione della partenza e itinerario;
- compatibilità tra fine del capitolo 10 e inizio del capitolo 11.

Non assumere come canon il file esterno `10_the-osiris-mysteries-revised.md`. Se l'app non può accedere all'originale, la continuità resta **non verificata**.

## 9.9 Lunghezza e revisione

La lunghezza indicativa del capitolo è 7.000 parole, con intervallo normale di 6.300–7.700 parole. La V4 è stata stimata intorno a 7.485 parole, quindi entro l'intervallo indicativo. Questo dato non autorizza a ignorare i problemi strutturali.

Le riparazioni devono essere mirate: eliminare ridondanza funzionale, ma sostituire il materiale debole con scene, indizi, azioni, decisioni, conseguenze o payoff quando serve preservare il peso narrativo.

---

# 10. Rischi della proposta e mitigazioni

## Rischio 1 — La metodologia generata diventa prolissa o sovra-ingegnerizzata

**Mitigazione:** richiedere solo criteri e registri che incidono su coerenza, comprensione, requisiti o payoff. Ogni registro deve dichiarare quale rischio previene. Consentire il riuso di metodologie di progetto precedenti, con adattamento esplicito.

## Rischio 2 — Il modello inventa invarianti o “corregge” il canon

**Mitigazione:** la metodologia è una proposta subordinata al canon e ai vincoli dell'autore. Le contraddizioni non risolvibili vengono segnalate; non si inventano valori mancanti.

## Rischio 3 — Falsa precisione nei punteggi

**Mitigazione:** i punteggi possono aiutare a ordinare le priorità, ma non sostituiscono blocker, test e motivazioni. Conservare evidenze e spiegazioni, non solo un numero.

## Rischio 4 — Il validatore deterministico verifica il modello sbagliato

**Mitigazione:** prima di applicare un invariante, dichiarare definizioni, unità e assunzioni. Per esempio, la somma dei nodi è valida solo se i gruppi sono disgiunti e completi.

## Rischio 5 — Il riconoscimento semantico perde precisione

**Mitigazione:** usare ID canonici dei concetti, alias espliciti, contesto e citazioni di supporto. Le corrispondenze a bassa confidenza diventano ambigue, non automaticamente vere.

## Rischio 6 — La riparazione migliora un criterio ma danneggia altri

**Mitigazione:** test di regressione su canon, continuità, lunghezza, voce, registri e scene dipendenti. Un cambiamento non è accettato finché i test rilevanti non passano.

## Rischio 7 — Il sistema si blocca per problemi minori

**Mitigazione:** gate proporzionati alla gravità. Solo i blocker fermano la generazione; i problemi minori vengono registrati e gestiti nella fase adeguata.

## Rischio 8 — La continuità viene dichiarata verificata usando file sbagliati

**Mitigazione:** marcare esplicitamente la fonte canonica di ogni capitolo. Se la fonte originale nell'app non è disponibile, lo stato deve essere `not_verified`, mai `passed`.

## Rischio 9 — Il valutatore conferma la propria riparazione

**Mitigazione:** separare, quando possibile, agente di riparazione e revisore indipendente; fornire al revisore i requisiti e le evidenze, non soltanto la giustificazione del generatore.

## Rischio 10 — Troppi controlli rallentano il lavoro

**Mitigazione:** eseguire controlli incrementali sui cambiamenti, mantenere una suite completa per le milestone e distinguere controlli rapidi da audit approfonditi.

---

# 11. Struttura dati minima consigliata

La forma esatta dipende dal codice esistente. Questa è una specifica concettuale, non un vincolo sul linguaggio o sul database.

## Metodo editoriale

- `methodology_id`, versione e timestamp;
- progetto e versione del testo;
- profili usati;
- criteri, motivazioni e pesi;
- registri e invarianti;
- motivi da sottoporre ad audit;
- fonti canoniche;
- rischi e condizioni di blocco;
- test di accettazione;
- decisioni dell'autore e deroghe.

## Difetto

- `issue_id` stabile;
- categoria e gravità;
- evidenza e localizzazione;
- requisito/invariante violato;
- impatto;
- riparazione proposta;
- test di accettazione;
- rischio di regressione;
- stato;
- risultato della verifica.

## Registro

- `register_id`;
- definizione dell'entità;
- unità e categorie;
- stato iniziale e fonte;
- eventi di transizione;
- invarianti;
- valori estratti con evidenza;
- certezza e ambiguità;
- risultati dei test.

## Risultato di verifica

- test eseguito;
- versione del testo e della metodologia;
- input e assunzioni;
- esito `pass`, `fail`, `ambiguous` o `not_run`;
- evidenza;
- dettaglio del fallimento;
- eventuale azione richiesta.

Non codificare questi campi in uno schema rigido prima di ispezionare l'architettura esistente. Integrare la specifica nel design attuale con la modifica minima che mantenga le responsabilità separate.

---

# 12. Criteri di accettazione del sistema

Il sistema può considerarsi pronto per un test pilota quando:

1. Un piano con un blocker noto non passa alla stesura senza riparazione verificata o deroga esplicita.
2. Una riparazione viene valutata tramite test di accettazione, non tramite semplice diminuzione del numero di difetti.
3. Un nuovo blocker impedisce l'accettazione anche se molti difetti minori sono stati risolti.
4. Le somme e le transizioni dei registri scoperti dalla metodologia sono verificate dal codice.
5. Un ID di ricerca inesistente viene risolto solo se il registro autorevole conferma la corrispondenza; le ambiguità sono segnalate.
6. Concetti narrativi equivalenti in lingue diverse vengono riconosciuti attraverso ID semantici e alias.
7. Il sistema distingue canon, evidenza esterna, ipotesi e dato non verificato.
8. Ogni difetto importante ha evidenza, gravità e test di accettazione.
9. La riparazione attiva una verifica di regressione sui requisiti interessati.
10. Il capitolo 11 non viene dichiarato continuo con il capitolo 10 finché l'originale canonico dell'app non è stato consultato.
11. La metodologia cambia in modo motivato fra un romanzo, un manuale e un saggio.
12. La revisione narrativa fornisce ragioni e prove, non solo un punteggio aggregato.
13. La lunghezza e gli altri vincoli espliciti sono verificati prima dell'accettazione.
14. Ogni deroga dell'autore e ogni decisione di canon è registrata e recuperabile.

---

# 13. Istruzioni operative per Claude Code

Procedere in modo incrementale. Prima di modificare il codice:

1. Ispezionare l'architettura attuale e identificare i componenti responsabili di analisi, piano, riparazione, stesura e verifica.
2. Mappare dove vengono conservati i difetti, i punteggi, gli ID di ricerca e gli stati del workflow.
3. Identificare il punto esatto in cui la stesura può partire nonostante blocker noti.
4. Proporre una modifica minima che introduca il gate e i test di accettazione senza riscrivere inutilmente il sistema.
5. Implementare prima il gate e la tracciabilità, quindi i validatori deterministici.
6. Aggiungere la metodologia adattiva come componente versionato e separato.
7. Creare test automatici con i casi V4 elencati sopra.
8. Eseguire i test e riportare chiaramente quelli superati, falliti o non eseguibili.
9. Non dichiarare risolta la continuità con il capitolo 10 senza consultare l'originale nell'app.
10. Non modificare capitoli originali o altri file canonici come effetto collaterale di un test, salvo richiesta esplicita dell'autore.

**Output atteso da Claude Code:**
- breve mappa dell'architettura esistente;
- piano di implementazione ordinato per priorità;
- file/componenti che verranno modificati;
- implementazione incrementale;
- test automatici;
- report dei risultati;
- limiti e decisioni che richiedono l'autore.

Non limitarsi a produrre un altro elenco di buone pratiche: trasformare i requisiti in stati, gate, test e verifiche ripetibili.

---

# 14. Regola conclusiva

Il sistema deve imparare dal testo che cosa va controllato, ma non deve lasciare all'LLM l'ultima parola su ciò che può essere verificato in modo deterministico.

**L'LLM interpreta e propone. Il canon vincola. Il codice verifica. Un revisore indipendente cerca regressioni. L'autore decide le ambiguità sostanziali.**

L'obiettivo non è eliminare il giudizio editoriale, né automatizzare ogni decisione letteraria. È impedire che difetti strutturali già noti attraversino il workflow senza una decisione consapevole e tracciabile.
