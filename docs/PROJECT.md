# CryptoBot Project

Ultimo aggiornamento: 2026-06-04

## Obiettivo

Rendere CryptoBot un sistema di paper trading piu robusto, leggibile e progressivamente piu opportunistico, senza perdere il controllo del rischio.

Il progetto non deve limitarsi a "non perdere": deve aiutare a identificare opportunita reali, filtrare segnali poveri, proteggere profitti aperti e dare all'utente strumenti chiari per capire quando intervenire.

## Contesto operativo

- Repository: `KrisRux/crypto-trading-bot`
- Branch operativo: `master`
- Deploy server: `/opt/cryptobot`
- URL pubblico: `https://claudcryptobot.duckdns.org/`
- Comando deploy:

```bash
sudo bash /opt/cryptobot/deploy/update.sh
```

Il bot gira su server Oracle e viene gestito tramite API e frontend.

## Stato Attuale

Sono gia state introdotte diverse aree di miglioramento:

- analisi PnL mark-to-market, per considerare anche posizioni aperte;
- correzione del calcolo delle metriche adaptive in percentuale rispetto all'equity;
- gestione profili operativi tramite API e UI;
- guardrail per posizioni stale;
- range profit exit per catturare profitti aperti;
- opportunita di mercato esposte in dashboard/API;
- miglioramento del contesto DeepSeek;
- output DeepSeek in italiano;
- campi snapshot compilati nei suggerimenti di tuning;
- correzione endpoint profili;
- aggiornamento dipendenze frontend vulnerabili (`vite`, `postcss`, `nanoid`);
- ignore dei file runtime locali (`*.db.bak`, cache Embient locale).

## Decisioni Prese

- Il branch da mantenere e `master`.
- Le configurazioni operative devono essere modificabili da UI/API, non tramite modifiche al codice.
- LTCUSDT puo restare escluso temporaneamente, ma deve essere riabilitabile da configurazione.
- Il bot deve notificare su Telegram eventi importanti, ma senza generare rumore inutile.
- DeepSeek deve lavorare come advisor, non come esecutore cieco.
- Le modifiche difensive non bastano: serve anche una vista opportunistica del mercato.
- Il server di produzione deve restare pulito da modifiche locali non intenzionali, per evitare blocchi di deploy.

## Aree Funzionali

### Trading Engine

Responsabile dell'esecuzione logica delle strategie, gestione posizioni, short/long paper e controlli runtime.

Priorita:

- migliorare la qualita dei segnali;
- evitare churn e fee drag;
- gestire profit lock e uscite da posizioni ferme;
- distinguere tra mercato trend, range, breakout e risk-off.

### Adaptive Layer

Responsabile di profili, advisor, guardrail, metriche recenti e suggerimenti.

Priorita:

- usare metriche percentuali coerenti con l'equity;
- evitare pause false causate da PnL assoluto;
- spiegare bene perche il bot cambia profilo;
- rendere ogni suggerimento verificabile con dati reali.

### DeepSeek Advisor

Responsabile dell'analisi assistita da LLM.

Priorita:

- rispondere sempre in italiano;
- ricevere contesto sufficiente su mercato, regime, posizioni, PnL, segnali bloccati e profili;
- produrre suggerimenti concreti, non generici;
- indicare confidenza, impatto atteso e rischio;
- distinguere tra osservazioni, raccomandazioni e azioni configurabili.

### UI

Responsabile della leggibilita operativa.

Sezioni rilevanti:

- Dashboard
- Operativo
- Strategia
- Controllo
- Sistema
- Profili
- Guardrail
- Opportunita

Priorita:

- rendere visibili le metriche che spiegano perche il bot non entra;
- mostrare quando un simbolo e candidato alla riabilitazione;
- consentire modifiche ai profili senza curl;
- aggiungere date chiare nei thread recenti;
- evidenziare blocchi dominanti come `trade_gate`.

### Telegram

Responsabile delle notifiche operative.

Notifiche utili:

- bot pausato o riattivato;
- cambio profilo;
- posizione stale;
- profit lock attivato;
- simbolo escluso o diventato candidate;
- errore critico API/exchange;
- drawdown rilevante;
- advisor con suggerimento ad alta confidenza.

Da evitare:

- notifiche ripetute ogni ciclo;
- warning senza azione suggerita;
- duplicati su stesso evento;
- messaggi troppo lunghi per eventi frequenti.

## Backlog Prioritario

### P0 - Stabilita Operativa

- Verificare che `git status --short` sul server resti pulito dopo deploy.
- Ripristinare o gestire separatamente i file `app/embient_skills/data/.../SKILL.md` modificati localmente sul server.
- Confermare che `npm audit` resti a zero vulnerabilita nel frontend.
- Verificare che la pagina Profili mostri correttamente stato e azioni.

### P1 - Profitabilita e Opportunismo

- Aggiungere una pipeline di market opportunity scoring piu esplicita.
- Separare segnali long e short per regime.
- Integrare news/sentiment come filtro di opportunita, non solo come freno.
- Misurare i motivi di blocco per simbolo e strategia.
- Identificare setup ad alta qualita anche se il profilo globale e defensive.

### P1 - Gestione Posizioni

- Raffinare profit lock trigger.
- Migliorare exit da posizioni stale.
- Rendere visibile in UI perche una posizione resta aperta.
- Notificare via Telegram quando una posizione passa in stato da monitorare.

### P2 - DeepSeek

- Migliorare prompt e contesto con:
  - riepilogo equity;
  - PnL 6h/24h/7d;
  - open positions;
  - strategie abilitate;
  - blocchi dominanti;
  - opportunita rilevate;
  - profilo corrente;
  - config proposta rispetto a config attuale.
- Validare che ogni risposta sia in italiano.
- Aggiungere un formato strutturato per suggerimenti applicabili.

### P2 - UI

- Aggiungere viste dedicate per:
  - profili e parametri;
  - opportunity scanner;
  - simboli esclusi/candidate;
  - motivi di blocco;
  - stato notifiche Telegram.

## Runbook Deploy

Prima del deploy:

```bash
cd /opt/cryptobot
git status --short
```

Se ci sono modifiche locali su file sorgente, fermarsi e decidere se:

- sono runtime/cache e vanno ignorate;
- sono modifiche volute e vanno committate;
- sono rumore e vanno ripristinate.

Deploy:

```bash
sudo bash /opt/cryptobot/deploy/update.sh
```

Dopo il deploy:

```bash
git log -3 --oneline
git status --short
```

Frontend:

```bash
cd /opt/cryptobot/frontend
sudo -u cryptobot npm audit
sudo -u cryptobot npm run build
```

## Note Sicurezza

- Non salvare token JWT in repository.
- Non includere token nei commit o nella documentazione.
- I token usati in chat servono solo per analisi runtime temporanea.
- I file database e backup locali non devono essere tracciati.

## Prossima Sessione Consigliata

1. Controllare stato live del bot dopo almeno 12-24 ore.
2. Analizzare:
   - PnL chiuso;
   - PnL mark-to-market;
   - posizioni aperte;
   - motivi di blocco;
   - simboli piu/meno performanti;
   - output DeepSeek;
   - notifiche Telegram ricevute.
3. Decidere se aumentare aggressivita su setup con edge reale.
4. Portare in UI eventuali parametri ancora modificabili solo da API.

