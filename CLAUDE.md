# Claude Instructions

## Obiettivo del progetto
Crea e mantieni un bot/agente per trading di criptovalute su Binance, con web app responsive per desktop e mobile, modalità live e modalità demo/paper trading, gestione autonoma di ordini e logiche di ingresso/uscita, e modulo per rilevare pattern di investimento.

## Riferimenti di progetto
- Repository principale (skills e agenti): https://github.com/SKE-Labs/agent-trading-skills
- Fai sempre riferimento a questo repository per skills, pattern e implementazioni condivise.

## Documentazione API da usare
Per tutte le integrazioni con Binance devi fare riferimento esclusivamente a questi link:

- Testnet Binance Spot: https://testnet.binance.vision/
- Documentazione Binance Spot API: https://binance-docs.github.io/apidocs/spot/en/

Quando implementi codice, assicurati di usare:
- endpoint testnet per la modalità di prova;
- endpoint spot ufficiali per la modalità live;
- WebSocket e REST coerenti con la documentazione ufficiale;
- gestione corretta di firma, timestamp, rate limit ed errori API.

## Regole importanti
- Non inserire mai chiavi API reali nel codice.
- Le chiavi devono essere lette da variabili d'ambiente o file `.env` o da db.
- In modalità test/demo il sistema deve inviare ordini utilizzando il puntamento di test.
- In modalità live il sistema deve operare solo con permessi di trading, mai con permessi di withdrawal.
- Ogni funzione critica deve avere gestione errori e logging.
- Prima di eseguire azioni irreversibili, verifica sempre la modalità attiva.

## Funzionalità richieste
### Trading
- Il bot deve piazzare ordini in modo autonomo.
- Deve poter chiudere posizioni quando raggiunge un margine di profitto configurabile.
- Deve supportare almeno:
  - market order
  - limit order
  - stop loss
  - take profit

### Pattern detection
- Implementa una sezione dedicata ai pattern di investimento.
- Deve calcolare indicatori tecnici base come:
  - SMA
  - EMA
  - RSI
  - MACD
- Deve generare segnali tipo:
  - BUY
  - SELL
  - HOLD

### Web app
- La web app deve essere responsive e utilizzabile sia da PC sia da cellulare.
- Deve includere:
  - dashboard saldo e posizioni;
  - storico trade e ordini;
  - configurazione strategie;
  - selezione modalità live / test / paper;
  - log esecuzione.

### Modalità demo
- Implementa una modalità di finto investimento per fare prove.
- La modalità demo deve:
  - simulare ordini e posizioni;
  - usare saldo virtuale;
  - salvare storico dei trade simulati;
  - permettere reset del portafoglio demo.

## Struttura consigliata
Organizza il progetto in modo modulare, ad esempio:

- `app/main.py`
- `app/binance_client/`
- `app/strategies/`
- `app/trading_engine/`
- `app/paper_trading/`
- `app/models/`
- `frontend/`
- `tests/`

## Git
- Dopo ogni modifica al codice, esegui sempre `git add`, `git commit` e `git push` per mantenere il repository aggiornato.
- Non lasciare modifiche locali senza push.

## Stile di lavoro
- Scrivi codice pulito, modulare e documentato.
- Se una richiesta è ambigua, fai una sola domanda chiarificatrice prima di procedere.
- Preferisci soluzioni semplici, robuste e facili da mantenere.
- Se proponi una strategia, separa sempre:
  - acquisizione dati,
  - calcolo indicatori,
  - generazione segnali,
  - esecuzione ordini.

## Output atteso
Quando modifichi o generi codice, fornisci:
- file completi;
- istruzioni di avvio;
- esempio di configurazione `.env`;
- eventuali test minimi;
- README aggiornato.

## Nota finale
Questo progetto deve essere pensato per uso sicuro e responsabile. La modalità testnet/paper trading deve essere sempre disponibile e ben evidenziata nell'interfaccia.
