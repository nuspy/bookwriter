# Review comparativa — Chapter 11 V4, *The Excavation*

**Confronto:** V1, V2, V3 e V4  
**Scopo:** valutare miglioramenti, regressioni e problemi persistenti, indicando gli interventi prioritari per il workflow di revisione dell'app.

## Verdetto

**V4 migliora concretamente la struttura e rientra nel limite di lunghezza, ma non è ancora pronta per una revisione stilistica finale.** Ha eliminato alcune duplicazioni narrative di V3 e ha un finale più mirato. Tuttavia, introduce contraddizioni verificabili nella cronologia e nel conteggio delle schede d'archivio, mentre la ripetizione concettuale del tema del *filing* è peggiorata.

**Limite metodologico:** non è stato possibile verificare Chapter 10 contro il capitolo originale presente nell'app. I rilievi di continuità con Chapter 10 devono quindi essere considerati provvisori finché non vengono controllati sul canon originale dell'app. Le versioni `revised` esterne non devono sostituire gli originali come fonte canonica.

## 1. Evoluzione delle versioni

| Versione | Parole stimate | Valutazione sintetica |
|---|---:|---|
| V1 | 6.669 | Impianto iniziale; protagonista e progressione dell'indagine meno sviluppati. |
| V2 | 7.515 | Più narrazione concreta; restano problemi di continuità e logica degli eventi. |
| V3 | 8.196 | Ponte Egitto–Francia più esplicito, ma lunghezza e duplicazioni strutturali. |
| V4 | 7.485 | Linea contemporanea più concentrata e finale più concreto; persistono contraddizioni interne. |

*Conteggi indicativi ottenuti con analisi testuale; il conteggio interno dell'app può differire leggermente.*

V4 è circa 711 parole più corta di V3 (riduzione di circa l'8,7%). Con un target di 7.000 parole e una tolleranza ordinaria ±10%, rientra nella fascia 6.300–7.700 parole.

## 2. Miglioramenti reali rispetto a V3

### 2.1 Linea contemporanea più leggibile

V3 alternava più scene moderne in luoghi e uffici simili. V4 concentra la linea contemporanea nell'archivio di Rue de Buci, a Parigi, attorno a un problema più immediato: le schede vengono rimosse mentre Nicolò cerca di capire dove finiscono.

### 2.2 Il finale contiene un indizio identificabile

La scheda con il nome *Cirati* trasforma la minaccia astratta in un elemento concreto. Nicolò vede un nome inserito nel sistema di catalogazione e intuisce che il prossimo trasferimento potrebbe riguardare una persona, non soltanto un oggetto.

**Da preservare:** il nome sulla scheda, il modo in cui Nicolò lo vede e la sua reazione. Prima di ampliare il significato del nome, verificarne la coerenza con il canon e con gli indizi già seminati.

### 2.3 Nicolò è un osservatore attivo

Fotografa documenti, nota le clip spostate, confronta le schede e ricostruisce l'intervallo in cui sono state rimosse. L'indagine si basa su dettagli osservabili e non soltanto su intuizioni filosofiche.

### 2.4 La lunghezza è tornata sotto controllo

V4 rientra nella fascia prevista senza scendere sotto il minimo. Non è consigliabile un ulteriore taglio generalizzato: il lavoro necessario ora è soprattutto di correzione logica e sostituzione delle ripetizioni, non di compressione indiscriminata.

## 3. Problemi critici persistenti o introdotti

### 3.1 Cronologia del viaggio non sufficientemente coerente

La scena iniziale indica una partenza dal Cairo alle 10:40, un arrivo a Roissy alle 19:55 e un volo descritto come di sei ore e quindici minuti. Poi Nicolò raggiunge Rue de Buci e l'orologio segna le 2:14. Il testo non chiarisce in modo affidabile se si tratti della notte successiva o del pomeriggio del giorno dopo. La sequenza non è necessariamente impossibile se esiste una pausa notturna, ma la pausa non è raccontata e gli orari non sono compatibili così come presentati.

Nel capitolo si alternano orari come `2:14`, `5:10`, `7:45` e notazioni come `18:00` e `20:15`. Il lettore deve dedurre se i primi siano orari pomeridiani o notturni.

**Intervento richiesto:** stabilire una timeline unica prima di modificare la prosa.

| Evento | Dato presente | Verifica necessaria |
|---|---|---|
| Partenza dal Cairo | 10:40 a.m. | Data e fuso orario |
| Arrivo a Roissy | 19:55 locale | Durata reale e cambio di fuso |
| Arrivo a Rue de Buci | 1:55, poi 2:14 | Giorno e formato orario |
| Rimozione delle schede | 4:00–5:10 | Coerenza con la permanenza di Nicolò |
| Chiusura/uscita | 18:00, 20:00, 20:15 | Unificare le scadenze |

Non correggere gli orari isolatamente: tutti gli eventi dipendono dalla stessa sequenza.

### 3.2 Le schede dell'archivio si contraddicono

La V4 contiene affermazioni incompatibili:

- Nella prima scena, l'indice contiene **undici schede** e ne mancano tre.
- In *The Pruned Drawer*, alle 2:15 l'indice viene descritto come contenente **quattordici schede**.
- Alle 5:10 ne rimangono undici dopo la rimozione di tre schede.
- Più avanti, alle 7:45, si dice che otto schede sono già state messe in una cassa alle 5:10 **prima dell'arrivo di Nicolò**, benché Nicolò sia nell'archivio da ore.

Queste affermazioni non possono essere tutte vere nella stessa cronologia.

Prima di riscrivere, decidere:
1. Quante schede sono presenti quando Nicolò entra?
2. Quali mancano all'inizio?
3. Quante vengono rimosse durante la sua permanenza?
4. Chi le rimuove e quando?
5. Quali schede rimangono alla fine e quali vengono trasferite nelle casse?

**Raccomandazione per il workflow:** creare un registro deterministico delle schede con identificativo e stato a ogni passaggio. Generare poi le descrizioni narrative a partire da quello stato. È un errore adatto a un quality gate automatico.

### 3.3 Il numero dei nodi non è coerente

La sezione *The Filing* distribuisce i nodi in nove posizioni francesi, cinque nell'area mediterranea orientale e due nei territori occidentali: totale **16**, mentre il testo parla di 14 nodi.

La sezione *The Commission* afferma invece che nel 1312 la Francia conserva sei nodi, Aragon e Catalogna ne ricevono tre e Cipro ne riceve cinque: totale 14. Ma poco prima si parla di sette nodi francesi, senza spiegare il passaggio da sette a sei.

Serve un unico schema canonico:

| Momento storico | Totale atteso | Da riconciliare |
|---|---:|---|
| Assetto iniziale | 14 | Distribuzione di partenza |
| Trasferimento del 1119 | 14 | Distribuzione descritta nella scena |
| Assetto del 1312 | 14 | Giurisdizioni e passaggi di mano |
| Trasferimento del 1317 | 14 | Quali nodi si spostano e da dove |
| Archivio contemporaneo | 14 riferimenti, salvo rimozioni | Distinguere nodi, schede e registrazioni |

Una **scheda d'archivio**, un **riferimento** e un **nodo del pattern** non sono necessariamente la stessa cosa. Il testo attuale li tratta spesso come intercambiabili.

### 3.4 Ripetizione concettuale del *filing*

I conteggi lessicali indicativi mostrano:

| Espressione | V2 | V3 | V4 |
|---|---:|---:|---:|
| “the pattern” | 43 | 40 | 94 |
| “filing” | 36 | 45 | 55 |

Sono conteggi grezzi, non un giudizio automatico. In questo caso, però, confermano un problema già evidente alla lettura: V4 ripete più volte che il pattern viene archiviato, trasferito, classificato e reso noioso per sopravvivere.

*The Filing*, *The Workshop and the Filing* e *The Commission* presentano varianti della stessa tesi. La ripetizione non sempre produce una nuova rivelazione o una conseguenza.

La parola *filing* può restare centrale, ma ogni ricorrenza importante dovrebbe fare qualcosa di nuovo: fornire un indizio, cambiare la comprensione di Nicolò, esporre qualcuno, spostare una prova o costringerlo a scegliere.

### 3.5 Le scene storiche non avanzano abbastanza la trama

L'ordine delle sezioni è:
1. Parigi contemporanea;
2. Gerusalemme, 1119;
3. Parigi, dopo il 1312;
4. Parigi contemporanea;
5. Parigi, 1310;
6. Parigi contemporanea.

Il salto temporale può funzionare. Il problema è che il lettore passa dalla scena post-1312 a quella del 1310, mentre entrambe ribadiscono in larga misura la stessa spiegazione. L'interludio storico non fornisce una nuova causa o conseguenza abbastanza forte da giustificare la ripresa.

**Intervento:** ordinare le scene storiche secondo una progressione intenzionale oppure rendere evidente che sono frammenti tematici non cronologici. Ogni interludio deve rivelare qualcosa che le altre scene non hanno già stabilito.

### 3.6 Il finale è più concreto, ma la minaccia resta soprattutto concettuale

Il nome *Cirati* è un buon gancio. Anche l'idea che il sistema stia preparando un fascicolo su Nicolò funziona. Tuttavia, il capitolo termina con Nicolò che aspetta il prossimo trasferimento, mentre il rischio resta soprattutto amministrativo e concettuale.

Il finale può essere sospeso, ma dovrebbe esserci un cambiamento irreversibile: ad esempio, Nicolò scopre che la sua identità è già stata registrata come oggetto del trasferimento, oppure perde l'accesso a una prova che aveva appena documentato. Non serve aggiungere un inseguimento artificiale; serve una conseguenza concreta.

## 4. Continuità con Chapter 10: verifica ancora da confermare

V4 afferma che Salma ha consegnato a Nicolò un fascicolo nell'ufficio di Dendera. Questo dettaglio deve essere verificato **contro Chapter 10 originale nell'app**. Non è possibile confermare qui che corrisponda alla versione canonica; una versione `revised` esterna non deve sostituire l'originale.

La verifica deve controllare:
- che cosa Salma consegna effettivamente a Nicolò;
- se il fascicolo e il pacchetto di cera siano lo stesso oggetto o due oggetti distinti;
- dove si trovino Salma e Yusuf alla fine del capitolo originale;
- quali informazioni Nicolò abbia acquisito e quali gli manchino;
- quale evento lo spinga a partire e quale percorso abbia seguito.

Finché il testo originale dell'app non è stato consultato, questi rilievi devono essere classificati **UNVERIFIED**, non come difetti confermati.

## 5. Valutazione finale

### Miglioramenti da preservare
- Lunghezza rientrata nel target.
- Linea contemporanea più concentrata.
- Indizio finale identificabile.
- Nicolò più attivo nell'osservazione.

### Bloccanti
- Cronologia del viaggio non stabilita.
- Conteggio delle schede contraddittorio.
- Distribuzione dei 14 nodi matematicamente incoerente.

### Da correggere dopo i bloccanti
- Ripetizione concettuale.
- Ordine e funzione degli interludi storici.
- Conseguenza concreta nel finale.
- Verifica della continuità sugli originali dell'app.

## 6. Ordine d'intervento consigliato per Claude Code

Non chiedere ancora al modello di “migliorare lo stile”. Procedere così:

1. Ricostruire la timeline di tutti gli eventi contemporanei, senza riscrivere la prosa finché gli orari non tornano.
2. Creare il registro delle schede e correggere ogni passaggio di stato.
3. Creare il registro dei 14 nodi, riconciliando le distribuzioni del 1119, 1312 e 1317.
4. Verificare Chapter 10 sull'originale dell'app e marcare come non verificati i dettagli senza evidenza.
5. Rivedere le scene storiche, mantenendo quelle che aggiungono informazioni o conseguenze distinte.
6. Eseguire l'audit semantico del *filing*, preservando il motivo ma eliminando le ripetizioni prive di progressione.
7. Effettuare una revisione narrativa mirata, mantenendo il capitolo tra 6.300 e 7.700 parole, salvo deroga approvata.
8. Eseguire un controllo indipendente delle regressioni e produrre il confronto prima/dopo.

## Conclusione

V4 è un passo avanti rispetto a V3 nella struttura e nella lunghezza, ma la prossima revisione deve essere guidata da dati di continuità e cronologia, non da un'altra riscrittura generale. Le correzioni più importanti sono verificabili e adatte a diventare test automatici dell'app.
